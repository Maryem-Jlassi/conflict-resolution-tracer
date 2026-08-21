"""
Provider adapter for heterogeneous LLM agent evaluation.

Supports multiple LLM providers with unified interface:
- Ollama (local models)
- OpenAI (gpt-4o-mini)
- Groq (qwen/qwen3.6-27b)

All API keys remain in environment variables and are never logged or stored.
"""

import os
import json
import hashlib
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Try to load .env from project root
    env_path = Path(__file__).parent.parent.parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Try current directory
        load_dotenv()
except ImportError:
    pass  # dotenv not available, rely on system environment


class ProviderType(Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    GROQ = "groq"


@dataclass
class GenerationRequest:
    """Structured request to LLM provider."""
    provider: str
    model: str
    agent_id: str
    role: str
    prompt: str
    temperature: float = 0.1
    max_tokens: int = 512
    timeout: int = 30


@dataclass
class GenerationResponse:
    """Structured response from LLM provider."""
    provider: str
    model: str
    agent_id: str
    role: str
    raw_response: str
    parsed_response: Optional[Dict[str, Any]]
    success: bool
    error: Optional[str]
    latency_ms: float
    timestamp: str
    prompt_hash: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider_metadata: Optional[Dict[str, Any]] = None


class ProviderAdapter:
    """Unified interface for multiple LLM providers."""
    
    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.available_providers = self._check_availability()
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load provider configuration from JSON file."""
        if config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        # Default configuration
        return {
            "providers": {
                "ollama_llama3.2_1b": {
                    "provider": "ollama",
                    "model": "llama3.2:1b",
                    "status": "available",
                    "endpoint": "http://localhost:11434",
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "timeout": 60
                },
                "ollama_llama3.2_latest": {
                    "provider": "ollama",
                    "model": "llama3.2:latest",
                    "status": "available",
                    "endpoint": "http://localhost:11434",
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "timeout": 60
                },
                "ollama_llama3.1_8b": {
                    "provider": "ollama",
                    "model": "llama3.1:8b",
                    "status": "available",
                    "endpoint": "http://localhost:11434",
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "timeout": 60
                },
                "openai_gpt4o_mini": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "status": "available",
                    "api_key_env": "OPENAI_API_KEY",
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "timeout": 30
                },
                "groq_qwen3.6_27b": {
                    "provider": "groq",
                    "model": "qwen/qwen3.6-27b",
                    "status": "available",
                    "api_key_env": "GROQ_API_KEY",
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    "timeout": 30
                }
            }
        }
    
    def _check_availability(self) -> Dict[str, bool]:
        """Check which providers are actually available."""
        available = {}
        
        for key, config in self.config["providers"].items():
            provider_type = config["provider"]
            
            if provider_type == "ollama":
                # Check if Ollama is running (with timeout to avoid hanging)
                try:
                    available[key] = self._check_ollama(config.get("endpoint", "http://localhost:11434"))
                except Exception:
                    available[key] = False  # Ollama not available, skip
            elif provider_type == "openai":
                # Check if API key is present
                api_key = os.environ.get(config.get("api_key_env", ""))
                available[key] = bool(api_key and api_key.startswith("sk-"))
            elif provider_type == "groq":
                # Check if API key is present
                api_key = os.environ.get(config.get("api_key_env", ""))
                available[key] = bool(api_key and api_key.startswith("gsk_"))
            else:
                available[key] = False
        
        return available
    
    def _check_ollama(self, endpoint: str) -> bool:
        """Check if Ollama server is running (non-blocking)."""
        try:
            import httpx
            response = httpx.get(f"{endpoint}/api/tags", timeout=2)  # Reduced timeout
            return response.status_code == 200
        except Exception:
            # Assume Ollama is available if configured to avoid blocking
            # We'll find out during actual generation
            return True
    
    def get_available_models(self) -> List[str]:
        """Get list of available model configurations."""
        return [key for key, available in self.available_providers.items() if available]
    
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate response from appropriate provider."""
        provider_type = request.provider.lower()
        
        timestamp = datetime.utcnow().isoformat()
        prompt_hash = hashlib.sha256(request.prompt.encode()).hexdigest()
        
        start_time = datetime.utcnow()
        
        try:
            if provider_type == "ollama":
                response = await self._generate_ollama(request)
            elif provider_type == "openai":
                response = await self._generate_openai(request)
            elif provider_type == "groq":
                response = await self._generate_groq(request)
            else:
                raise ValueError(f"Unknown provider: {provider_type}")
            
            latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            return GenerationResponse(
                provider=request.provider,
                model=request.model,
                agent_id=request.agent_id,
                role=request.role,
                raw_response=response["text"],
                parsed_response=response.get("parsed"),
                success=True,
                error=None,
                latency_ms=latency_ms,
                timestamp=timestamp,
                prompt_hash=prompt_hash,
                input_tokens=response.get("input_tokens"),
                output_tokens=response.get("output_tokens"),
                provider_metadata=response.get("metadata")
            )
            
        except Exception as e:
            latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            return GenerationResponse(
                provider=request.provider,
                model=request.model,
                agent_id=request.agent_id,
                role=request.role,
                raw_response="",
                parsed_response=None,
                success=False,
                error=str(e),
                latency_ms=latency_ms,
                timestamp=timestamp,
                prompt_hash=prompt_hash
            )
    
    async def _generate_ollama(self, request: GenerationRequest) -> Dict[str, Any]:
        """Generate using Ollama."""
        try:
            import httpx
            
            endpoint = "http://localhost:11434/api/generate"
            payload = {
                "model": request.model,
                "prompt": request.prompt,
                "stream": False,
                "options": {
                    "temperature": request.temperature,
                    "num_predict": request.max_tokens
                }
            }
            
            async with httpx.AsyncClient(timeout=request.timeout) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
            
            return {
                "text": data.get("response", ""),
                "input_tokens": data.get("prompt_eval_count"),
                "output_tokens": data.get("eval_count"),
                "metadata": {
                    "total_duration_ms": data.get("total_duration"),
                    "load_duration_ms": data.get("load_duration"),
                    "eval_count": data.get("eval_count")
                }
            }
            
        except Exception as e:
            raise Exception(f"Ollama generation failed: {str(e)}")
    
    async def _generate_openai(self, request: GenerationRequest) -> Dict[str, Any]:
        """Generate using OpenAI."""
        try:
            from openai import AsyncOpenAI
            
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise Exception("OPENAI_API_KEY not found in environment")
            
            client = AsyncOpenAI(api_key=api_key)
            
            response = await client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout
            )
            
            return {
                "text": response.choices[0].message.content,
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "metadata": {
                    "finish_reason": response.choices[0].finish_reason,
                    "model": response.model
                }
            }
            
        except Exception as e:
            raise Exception(f"OpenAI generation failed: {str(e)}")
    
    async def _generate_groq(self, request: GenerationRequest) -> Dict[str, Any]:
        """Generate using Groq."""
        try:
            from groq import AsyncGroq
            
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise Exception("GROQ_API_KEY not found in environment")
            
            client = AsyncGroq(api_key=api_key)
            
            response = await client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.prompt}],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout
            )
            
            return {
                "text": response.choices[0].message.content,
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "metadata": {
                    "finish_reason": response.choices[0].finish_reason,
                    "model": response.model
                }
            }
            
        except Exception as e:
            raise Exception(f"Groq generation failed: {str(e)}")
    
    def parse_json_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse JSON from LLM response."""
        try:
            # Try direct JSON parsing
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', response_text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass
            return None