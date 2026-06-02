from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from typing import Optional
import os
import json

from auth import create_jwt, decode_jwt, verify_google_token
from database import (
    upsert_user, get_user_by_id,
    create_interview, get_interview, save_first_question,
    update_last_answer, append_question,
    save_feedback, get_student_interviews,
    get_analytics, get_all_students,
    save_contact_message, update_user_notes
)
from pdf_parser import extract_text_from_pdf
from gemini import get_next_question, generate_feedback
from tts_utils import text_to_speech_base64

app = FastAPI(title="Vocalis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
@app.head("/health")
async def health_check():
    return PlainTextResponse("OK")


def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ")[1]
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

@app.post("/auth/google")
async def google_auth(data: dict):
    id_token = data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="id_token required")
    google_user = await verify_google_token(id_token)
    if not google_user or "sub" not in google_user:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    user = upsert_user(
        google_id=google_user["sub"],
        name=google_user.get("name", ""),
        email=google_user.get("email", ""),
        picture=google_user.get("picture", ""),
    )
    token = create_jwt(user["_id"], user["role"])
    return {"token": token, "user": user}

@app.get("/auth/me")
async def get_me(user=Depends(get_current_user)):
    return user

@app.post("/student/notes")
async def save_student_notes(data: dict, user=Depends(get_current_user)):
    notes = data.get("notes", [])
    success = update_user_notes(user["_id"], notes)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save notes")
    return {"message": "Notes saved"}

@app.post("/interview/upload-resume")
async def upload_resume(
    file: UploadFile = File(...),
    company: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    interview_type: str = Form("both"),
    user=Depends(get_current_user),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")
    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    resume_text = extract_text_from_pdf(file_bytes)
    if not resume_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF")
    interview = create_interview(
        student_id=user["_id"],
        company=company,
        role=role,
        interview_type=interview_type,
        resume_text=resume_text,
    )
    return {"interview_id": interview["_id"], "message": "Resume parsed successfully"}

@app.post("/interview/start")
async def start_interview(data: dict, user=Depends(get_current_user)):
    interview_id = data.get("interview_id")
    interview = get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview["student_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="Not your interview")

    # Handle page refresh — return existing pending question
    convo = interview.get("conversation", [])
    if convo:
        last = convo[-1]
        if not last.get("answer", "").strip():
            return {"question": last["question"], "is_complete": False, "question_number": len(convo)}

    question, _ = get_next_question(
        name=user["name"],
        company=interview["company"],
        role=interview["role"],
        interview_type=interview["interview_type"],
        resume_text=interview["resume_text"],
        conversation_history=[],
    )
    save_first_question(interview_id, question)
    try:
        audio_b64 = await text_to_speech_base64(question)
    except Exception as e:
        print(f"TTS Error (Graceful Skip): {e}")
        audio_b64 = None
    return {"question": question, "is_complete": False, "question_number": 1, "audio_b64": audio_b64}

@app.post("/interview/next")
async def next_question(data: dict, user=Depends(get_current_user)):
    interview_id = data.get("interview_id")
    answer = (data.get("answer") or "").strip()
    audio_b64 = data.get("audio_b64")

    if not answer:
        raise HTTPException(status_code=400, detail="Answer cannot be empty")

    interview = get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview["student_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="Not your interview")

    convo = interview.get("conversation", [])
    if not convo:
        raise HTTPException(status_code=400, detail="Interview not started")

    # Fill answer into last unanswered turn
    update_last_answer(interview_id, answer, audio_b64)

    # Reload and get only answered turns for Gemini context
    updated = get_interview(interview_id)
    answered_convo = [t for t in updated["conversation"] if t.get("answer", "").strip()]

    next_q, is_complete = get_next_question(
        name=user["name"],
        company=interview["company"],
        role=interview["role"],
        interview_type=interview["interview_type"],
        resume_text=interview["resume_text"],
        conversation_history=answered_convo,
    )

    audio_b64 = None
    if not is_complete:
        append_question(interview_id, next_q)
        try:
            audio_b64 = await text_to_speech_base64(next_q)
        except Exception as e:
            print(f"TTS Error (Graceful Skip): {e}")
            audio_b64 = None

    return {
        "question": next_q,
        "is_complete": is_complete,
        "question_number": len(answered_convo) + 1,
        "audio_b64": audio_b64
    }

@app.websocket("/ws/interview/{interview_id}")
async def interview_websocket(websocket: WebSocket, interview_id: str, token: str = None):
    await websocket.accept()
    
    # 1. Validate Token
    if not token:
        await websocket.close(code=4001) # No token
        return
    
    payload = decode_jwt(token)
    if not payload:
        await websocket.close(code=4002) # Invalid token
        return
    
    user = get_user_by_id(payload["sub"])
    interview = get_interview(interview_id)
    if not interview or interview["student_id"] != user["_id"]:
        await websocket.close(code=4003) # Unauthorized
        return

    try:
        while True:
            # Receive answer from client
            msg = await websocket.receive_text()
            data = json.loads(msg)
            answer = data.get("answer", "").strip()
            
            if not answer:
                continue

            # Process logic (mirrors /interview/next but over WS)
            update_last_answer(interview_id, answer, None)
            updated = get_interview(interview_id)
            answered_convo = [t for t in updated["conversation"] if t.get("answer", "").strip()]

            next_q, is_complete = get_next_question(
                name=user["name"],
                company=interview["company"],
                role=interview["role"],
                interview_type=interview["interview_type"],
                resume_text=interview["resume_text"],
                conversation_history=answered_convo,
            )

            audio_b64 = None
            if not is_complete:
                append_question(interview_id, next_q)
                try:
                    audio_b64 = await text_to_speech_base64(next_q)
                except Exception as e:
                    print(f"TTS Error (Graceful Skip): {e}")
                    audio_b64 = None

            # Send back to client
            await websocket.send_json({
                "question": next_q,
                "is_complete": is_complete,
                "question_number": len(answered_convo) + 1,
                "audio_b64": audio_b64
            })

            if is_complete:
                break

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for interview {interview_id}")
    except Exception as e:
        print(f"WebSocket error: {e}")
        await websocket.close(code=4000)

@app.post("/interview/end")
async def end_interview(data: dict, user=Depends(get_current_user)):
    interview_id = data.get("interview_id")
    focus_score = data.get("focus_score", 100)
    eye_contact_score = data.get("eye_contact_score", 100) # NEW
    focus_timeline = data.get("focus_timeline", [])
    multiple_faces = data.get("multiple_faces", False)
    interview = get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview["student_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="Not your interview")

    answered = [t for t in interview.get("conversation", []) if t.get("answer", "").strip()]
    if len(answered) < 1:
        raise HTTPException(status_code=400, detail="Need at least 1 answered question for feedback")

    feedback = generate_feedback(
        name=user["name"],
        company=interview["company"],
        role=interview["role"],
        interview_type=interview["interview_type"],
        resume_text=interview["resume_text"],
        conversation=answered,
        focus_score=focus_score,
        eye_contact_score=eye_contact_score, # Pass to AI
    )
    save_feedback(interview_id, feedback, focus_score, focus_timeline, multiple_faces, eye_contact_score)
    return {"feedback": feedback, "interview_id": interview_id}

@app.get("/interview/history")
async def interview_history(user=Depends(get_current_user)):
    return {"interviews": get_student_interviews(user["_id"])}

@app.get("/interview/{interview_id}")
async def get_single_interview(interview_id: str, user=Depends(get_current_user)):
    interview = get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Not found")
    if interview["student_id"] != user["_id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    interview.pop("resume_text", None)
    return interview

@app.get("/admin/analytics")
async def admin_analytics(admin=Depends(require_admin)):
    return get_analytics()

@app.get("/admin/students")
async def admin_students(admin=Depends(require_admin)):
    return {"students": get_all_students()}

@app.get("/admin/interviews")
async def admin_all_interviews(admin=Depends(require_admin)):
    from database import interviews_col, serialize
    docs = list(interviews_col.find({}, {"resume_text": 0}).sort("created_at", -1).limit(100))
    return {"interviews": [serialize(d) for d in docs]}

@app.get("/admin/contacts")
async def admin_contacts(admin=Depends(require_admin)):
    from database import get_all_contacts
    return {"contacts": get_all_contacts()}

frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))

@app.post("/contact")
async def handle_contact(data: dict):
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    message = data.get("message", "").strip()
    
    if not name or not email or not message:
        raise HTTPException(status_code=400, detail="All fields are required")
    
    msg_id = save_contact_message(name, email, message)
    
    # Logic to "send" email (for now we log it, since SMTP creds aren't in .env)
    admin_emails = os.getenv("ADMIN_EMAILS", "gauravsaklani47@gmail.com")
    print(f"--- NEW CONTACT MESSAGE ---\nTo: {admin_emails}\nFrom: {name} ({email})\nID: {msg_id}\nMessage: {message}\n---------------------------")
    
    return {"message": "Message sent successfully", "id": msg_id}

if os.path.exists(frontend_path):
    # Mount sub-folders explicitly so /css/, /js/, /student/, /admin/ all resolve
    app.mount("/css", StaticFiles(directory=os.path.join(frontend_path, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(frontend_path, "js")), name="js")
    app.mount("/student", StaticFiles(directory=os.path.join(frontend_path, "student")), name="student")
    app.mount("/admin", StaticFiles(directory=os.path.join(frontend_path, "admin")), name="admin")
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_path, "assets")), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(frontend_path, "index.html"))

    @app.get("/login.html")
    async def serve_login():
        return FileResponse(os.path.join(frontend_path, "login.html"))

    @app.get("/index.html")
    async def serve_index2():
        return FileResponse(os.path.join(frontend_path, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
