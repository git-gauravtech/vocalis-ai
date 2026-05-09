from pymongo import MongoClient
from datetime import datetime
from bson import ObjectId
import os
from dotenv import load_dotenv

load_dotenv()

client = MongoClient(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
db = client["mock_interview"]
users_col = db["users"]
interviews_col = db["interviews"]

users_col.create_index("google_id", unique=True)
users_col.create_index("email", unique=True)


def serialize(doc):
    if doc is None:
        return None
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return doc


# ── USERS ──────────────────────────────────────────────
def get_user_by_google_id(google_id: str):
    return serialize(users_col.find_one({"google_id": google_id}))

def get_user_by_id(user_id: str):
    try:
        return serialize(users_col.find_one({"_id": ObjectId(user_id)}))
    except Exception:
        return None

def create_user(google_id, name, email, picture):
    admin_emails = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    role = "admin" if email.strip() in admin_emails else "student"
    doc = {
        "google_id": google_id, "name": name, "email": email,
        "picture": picture, "role": role, "created_at": datetime.utcnow(),
    }
    result = users_col.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc

def upsert_user(google_id, name, email, picture):
    user = get_user_by_google_id(google_id)
    if user:
        users_col.update_one({"google_id": google_id}, {"$set": {"name": name, "picture": picture}})
        return get_user_by_google_id(google_id)
    return create_user(google_id, name, email, picture)


# ── INTERVIEWS ─────────────────────────────────────────
def create_interview(student_id, company, role, interview_type, resume_text):
    doc = {
        "student_id": student_id,
        "company": company or "General",
        "role": role or "Software Engineer",
        "interview_type": interview_type,
        "resume_text": resume_text,
        "conversation": [],   # list of {question, answer, audio_b64}
        "feedback": None,
        "status": "active",
        "created_at": datetime.utcnow(),
        "ended_at": None,
    }
    result = interviews_col.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc

def get_interview(interview_id: str):
    try:
        return serialize(interviews_col.find_one({"_id": ObjectId(interview_id)}))
    except Exception:
        return None

def save_first_question(interview_id: str, question: str):
    """Store the very first question with empty answer."""
    turn = {"question": question, "answer": "", "audio_b64": None, "timestamp": datetime.utcnow().isoformat()}
    interviews_col.update_one(
        {"_id": ObjectId(interview_id)},
        {"$push": {"conversation": turn}},
    )

def update_last_answer(interview_id: str, answer: str, audio_b64: str = None):
    """Fill answer into the last turn that has an empty answer."""
    interview = get_interview(interview_id)
    if not interview:
        return
    convo = interview["conversation"]
    # Find last unanswered turn index
    last_idx = None
    for i in range(len(convo) - 1, -1, -1):
        if not convo[i].get("answer", "").strip():
            last_idx = i
            break
    if last_idx is None:
        return
    interviews_col.update_one(
        {"_id": ObjectId(interview_id)},
        {"$set": {
            f"conversation.{last_idx}.answer": answer,
            f"conversation.{last_idx}.audio_b64": audio_b64,
        }},
    )

def append_question(interview_id: str, question: str):
    """Append a new question turn with empty answer."""
    turn = {"question": question, "answer": "", "audio_b64": None, "timestamp": datetime.utcnow().isoformat()}
    interviews_col.update_one(
        {"_id": ObjectId(interview_id)},
        {"$push": {"conversation": turn}},
    )

def save_feedback(interview_id: str, feedback: dict, focus_score: int = None, focus_timeline: list = None, multiple_faces: bool = False, eye_contact_score: int = 100):
    interviews_col.update_one(
        {"_id": ObjectId(interview_id)},
        {"$set": {
            "feedback": feedback, 
            "focus_score": focus_score,
            "eye_contact_score": eye_contact_score,
            "focus_timeline": focus_timeline,
            "multiple_faces_detected": multiple_faces,
            "status": "completed", 
            "ended_at": datetime.utcnow()
        }},
    )

def get_student_interviews(student_id: str):
    docs = interviews_col.find({"student_id": student_id}, {"resume_text": 0, "conversation.audio_b64": 0}).sort("created_at", -1)
    return [serialize(d) for d in docs]


# ── ADMIN ANALYTICS ────────────────────────────────────
def get_analytics():
    total_students = users_col.count_documents({"role": "student"})
    total_interviews = interviews_col.count_documents({})
    completed = interviews_col.count_documents({"status": "completed"})

    pipeline = [
        {"$match": {"feedback.overall_score": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": None, "avg": {"$avg": "$feedback.overall_score"}}},
    ]
    avg_result = list(interviews_col.aggregate(pipeline))
    avg_score = round(avg_result[0]["avg"], 1) if avg_result else 0

    company_pipeline = [
        {"$group": {"_id": "$company", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 5},
    ]
    top_companies = [{"company": r["_id"], "count": r["count"]} for r in interviews_col.aggregate(company_pipeline)]

    role_pipeline = [
        {"$group": {"_id": "$role", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 5},
    ]
    top_roles = [{"role": r["_id"], "count": r["count"]} for r in interviews_col.aggregate(role_pipeline)]

    type_pipeline = [{"$group": {"_id": "$interview_type", "count": {"$sum": 1}}}]
    type_dist = {r["_id"]: r["count"] for r in interviews_col.aggregate(type_pipeline)}

    recent = list(interviews_col.find({}, {"resume_text": 0, "conversation": 0}).sort("created_at", -1).limit(10))

    return {
        "total_students": total_students,
        "total_interviews": total_interviews,
        "completed_interviews": completed,
        "avg_score": avg_score,
        "top_companies": top_companies,
        "top_roles": top_roles,
        "type_distribution": type_dist,
        "recent_interviews": [serialize(r) for r in recent],
    }

def get_all_students():
    students = list(users_col.find({"role": "student"}).sort("created_at", -1))
    result = []
    for s in students:
        s = serialize(s)
        s["interview_count"] = interviews_col.count_documents({"student_id": s["_id"]})
        result.append(s)
    return result
# ── CONTACTS ───────────────────────────────────────────
def save_contact_message(name, email, message):
    doc = {
        "name": name,
        "email": email,
        "message": message,
        "created_at": datetime.utcnow()
    }
    result = db["contacts"].insert_one(doc)
    return str(result.inserted_id)
def get_all_contacts():
    docs = list(db["contacts"].find().sort("created_at", -1))
    return [serialize(d) for d in docs]
