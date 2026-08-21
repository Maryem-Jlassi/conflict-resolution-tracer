"""
Simple direct test of providers without Ollama blocking.
"""

import asyncio
import os
from provider_adapter import ProviderAdapter, GenerationRequest

async def test_direct():
    """Test providers directly without availability checks."""
    print("Direct Provider Test")
    print("=" * 60)
    
    # Test OpenAI directly
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        print(f"\n[OK] OpenAI API key found (length: {len(openai_key)})")
        print("Testing OpenAI...")
        
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Respond with JSON: {\"test\": \"success\"}"}],
                temperature=0.1,
                max_tokens=50,
                timeout=30
            )
            
            print(f"[SUCCESS] OpenAI: {response.choices[0].message.content[:50]}...")
            print(f"   Tokens: {response.usage.prompt_tokens} in, {response.usage.completion_tokens} out")
            
        except Exception as e:
            print(f"[FAILED] OpenAI: {str(e)}")
    else:
        print("[MISSING] OpenAI API key not found")
    
    # Test Groq directly
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        print(f"\n[OK] Groq API key found (length: {len(groq_key)})")
        print("Testing Groq...")
        
        try:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=groq_key)
            
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                messages=[{"role": "user", "content": "Respond with JSON: {\"test\": \"success\"}"}],
                temperature=0.1,
                max_tokens=50,
                timeout=30
            )
            
            print(f"[SUCCESS] Groq: {response.choices[0].message.content[:50]}...")
            print(f"   Tokens: {response.usage.prompt_tokens} in, {response.usage.completion_tokens} out")
            
        except Exception as e:
            print(f"[FAILED] Groq: {str(e)}")
    else:
        print("[MISSING] Groq API key not found")
    
    # Test Ollama with timeout
    print(f"\nTesting Ollama (with timeout)...")
    try:
        import httpx
        response = httpx.get("http://localhost:11434/api/tags", timeout=2)
        if response.status_code == 200:
            print("[OK] Ollama server running")
            models = response.json().get("models", [])
            print(f"   Available models: {[m['name'] for m in models[:3]]}")
        else:
            print(f"[FAILED] Ollama returned status {response.status_code}")
    except Exception as e:
        print(f"[WARNING] Ollama check failed (may still work): {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_direct())