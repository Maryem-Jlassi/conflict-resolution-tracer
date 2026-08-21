"""
Simple test script to verify all providers work correctly.
"""

import asyncio
import os
from provider_adapter import ProviderAdapter, GenerationRequest

async def test_providers():
    """Test all available providers."""
    adapter = ProviderAdapter()
    
    print("Testing provider availability...")
    available = adapter.get_available_models()
    print(f"Available providers: {available}")
    
    if not available:
        print("No providers available!")
        return
    
    # Test each provider with a simple prompt
    test_prompt = "Respond with a single JSON object: {\"test\": \"success\"}"
    
    for provider_key in available:
        provider_config = adapter.config["providers"][provider_key]
        print(f"\nTesting {provider_key} ({provider_config['provider']} - {provider_config['model']})...")
        
        request = GenerationRequest(
            provider=provider_config["provider"],
            model=provider_config["model"],
            agent_id="test_agent",
            role="test",
            prompt=test_prompt,
            temperature=0.1,
            max_tokens=50,
            timeout=30
        )
        
        try:
            response = await adapter.generate(request)
            
            if response.success:
                print(f"✅ SUCCESS")
                print(f"   Response: {response.raw_response[:100]}...")
                print(f"   Latency: {response.latency_ms:.2f}ms")
                print(f"   Tokens: input={response.input_tokens}, output={response.output_tokens}")
            else:
                print(f"❌ FAILED: {response.error}")
                
        except Exception as e:
            print(f"❌ EXCEPTION: {str(e)}")

if __name__ == "__main__":
    print("Provider Test Script")
    print("=" * 60)
    asyncio.run(test_providers())