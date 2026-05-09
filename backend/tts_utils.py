import edge_tts
import base64
import io
import asyncio

async def text_to_speech_base64(text: str, voice: str = "en-US-GuyNeural") -> str:
    """
    Converts text to speech using edge-tts and returns a base64 encoded MP3 string.
    Voice options: en-US-AvaNeural, en-US-AndrewNeural, en-GB-SoniaNeural, etc.
    """
    communicate = edge_tts.Communicate(text, voice)
    
    # Use a byte buffer to store the audio
    audio_buffer = io.BytesIO()
    
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
            
    # Seek to start
    audio_buffer.seek(0)
    
    # Convert to base64
    audio_b64 = base64.b64encode(audio_buffer.read()).decode("utf-8")
    return audio_b64

# Simple test if run directly
if __name__ == "__main__":
    async def test():
        b64 = await text_to_speech_base64("Hello, this is Vocalis.")
        print(f"Generated {len(b64)} chars of base64 audio.")
    asyncio.run(test())
