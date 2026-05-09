import google.generativeai as genai
import os
import json
import time
import random
from dotenv import load_dotenv

load_dotenv()

# ✅ API KEY ROTATION LOGIC
def get_model():
    keys_str = os.getenv("GEMINI_API_KEYS", "")
    if keys_str:
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    else:
        keys = [os.getenv("GEMINI_API_KEY")]

    api_key = random.choice(keys)
    genai.configure(api_key=api_key)
    
    return genai.GenerativeModel(
        "models/gemini-2.5-flash",
        generation_config={
            "temperature": 0.7,
            "top_p": 0.9,
        }
    )

# Dynamic fallback questions
fallback_questions = [
    "Can you explain a challenging problem you solved recently?",
    "What technologies are you most comfortable with and why?",
    "Tell me about a project you're most proud of.",
    "How do you approach debugging a difficult issue?",
    "Describe a time you faced a technical challenge and how you solved it."
]

def safe_send(chat, prompt, retry_count=3):
    """
    Tries to send a message. If it fails due to an API key issue, 
    it re-configures with a different key and retries.
    """
    for attempt in range(retry_count):
        try:
            return chat.send_message(prompt)
        except Exception as e:
            error_msg = str(e).upper()
            print(f"Gemini error (Attempt {attempt+1}):", e)
            
            # If it's a key issue, try to switch keys immediately
            if "API_KEY" in error_msg or "400" in error_msg or "INVALID" in error_msg:
                print("Detected API key issue. Attempting to rotate key...")
                # Re-configuring with a new random key
                new_model = get_model()
                # We need to restart the chat with the new model and the old history
                # But to keep it simple, we'll just try to send the message again 
                # after a short sleep if the global config was updated.
                time.sleep(1)
                continue 
            
            time.sleep(2)
    
    # Final fallback if all retries fail
    print("All retries failed. Using fallback question.")
    return type("obj", (), {
        "text": random.choice(fallback_questions)
    })


def build_system_prompt(name, company, role, interview_type, resume_text):
    words = resume_text.split()
    if len(words) > 300:
        resume_text = " ".join(words[:300]) + "..."

    return f"""You are a senior interviewer at {company or 'a top tech company'} conducting a real job interview for {role or 'Software Engineer'}.

CANDIDATE NAME: {name}

RESUME:
{resume_text}

RULES:
- Ask ONLY one question at a time
- Be natural and professional
- NEVER repeat questions
- NEVER ask "Tell me about yourself"
- Always move forward logically
- Ask follow-ups if answer is weak
- Keep variety: technical, behavioral, scenario

INTERVIEW FLOW:
1. Resume/project discussion
2. Technical depth
3. Problem solving
4. Behavioral
5. Wrap-up

END:
After ~10 questions → output:
INTERVIEW_COMPLETE
"""


def get_next_question(name, company, role, interview_type, resume_text, conversation_history):
    system = build_system_prompt(name, company, role, interview_type, resume_text)

    question_count = len(conversation_history)

    # ✅ First question
    if question_count == 0:
        model = get_model()
        chat = model.start_chat(history=[])
        response = safe_send(
            chat,
            system + "\nStart interview. Greet briefly and ask a UNIQUE resume-based question."
        )
        return response.text.strip(), False

    # ✅ Track previous questions
    asked_questions = [turn["question"] for turn in conversation_history]
    asked_str = "\n".join(asked_questions[-6:])

    # ✅ Build limited history
    history = []
    for turn in conversation_history[-6:]:
        history.append({"role": "model", "parts": [turn["question"]]})
        history.append({"role": "user", "parts": [turn["answer"]]})

    model = get_model()
    chat = model.start_chat(history=history)

    prompt = f"""{system}

CURRENT QUESTION NUMBER: {question_count + 1}

PREVIOUS QUESTIONS:
{asked_str}

STRICT RULES:
- DO NOT repeat or rephrase previous questions
- If topic covered → go deeper OR switch topic
- Increase difficulty gradually
- If answer weak → ask follow-up
- If strong → move forward
- Keep questions short and realistic

If question count >= 10:
Respond with INTERVIEW_COMPLETE

Ask next question now.
"""

    response = safe_send(chat, prompt)
    text = response.text.strip()

    is_complete = "INTERVIEW_COMPLETE" in text
    clean_text = text.replace("INTERVIEW_COMPLETE", "").strip()

    return clean_text, is_complete

def generate_feedback(name, company, role, interview_type, resume_text, conversation, focus_score=100, eye_contact_score=100):
    convo_text = ""
    for i, turn in enumerate(conversation, 1):
        convo_text += f"\nQ{i}: {turn['question']}\nA{i}: {turn['answer']}\n"

    prompt = f"""
You are an expert interview coach.

Analyze this interview:

Candidate: {name}
Role: {role}
Company: {company}
Interview Type: {interview_type}
Focus Score (Presence): {focus_score}%
Eye Contact Score: {eye_contact_score}%

{convo_text}

IMPORTANT:
- Return ONLY valid JSON
- Do NOT include markdown, explanation, or text outside JSON
- Ensure JSON is complete and properly formatted
- Specifically mention eye contact and presence in the summary if scores are low.

Format:
{{
  "overall_score": 1-10,
  "verdict": "Strongly Recommend | Recommend | Maybe | Not Recommended",
  "summary": "short summary including behavioral feedback",
  "strengths": ["point1", "point2"],
  "weaknesses": ["point1", "point2"],
  "improvement_tips": ["tip1", "tip2"]
}}
"""

    # ✅ Retry logic for feedback generation
    for attempt in range(3):
        try:
            model = get_model()
            response = model.generate_content(prompt)
            text = response.text.strip()

            print("RAW GEMINI FEEDBACK:", text)  # 🔍 debug

            # ✅ Try extracting JSON safely
            start = text.find("{")
            end = text.rfind("}")

            if start != -1 and end != -1:
                json_text = text[start:end+1]
                return json.loads(json_text)

            raise ValueError("No JSON found")

        except Exception as e:
            error_msg = str(e).upper()
            print(f"Feedback error (Attempt {attempt+1}):", e)
            if "API_KEY" in error_msg or "400" in error_msg or "INVALID" in error_msg:
                print("Rotating key for feedback...")
                time.sleep(1)
                continue
            time.sleep(1)

        # ✅ fallback response
        return {
            "overall_score": 5,
            "verdict": "Maybe",
            "summary": "Feedback could not be generated properly.",
            "strengths": ["Attempted all questions"],
            "weaknesses": ["Analysis unavailable"],
            "improvement_tips": ["Try again for detailed feedback"],
        }