"""
Live agent harness for heterogeneous LLM agent evaluation.

Coordinates:
- LLM generation from multiple providers
- Agent role-based prompting
- Memory submission to CRT service
- Frozen corpus generation and replay
- Result collection and analysis
"""

import os
import json
import asyncio
import hashlib
import random
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
import sqlite3

from provider_adapter import ProviderAdapter, GenerationRequest, GenerationResponse
from agent_roles import (
    get_agent_role, get_full_prompt, get_task_prompt, 
    get_adversarial_prompt, AGENT_ROLES, TASK_SCENARIOS
)


@dataclass
class AgentOperation:
    """Record of an agent operation to be submitted to CRT."""
    experiment_id: str
    run_id: str
    provider: str
    model: str
    agent_id: str
    role: str
    prompt_hash: str
    prompt_text: str
    raw_response: str
    parsed_response: Optional[Dict[str, Any]]
    target_path: str
    claimed_value: str
    timestamp: str
    generation_latency_ms: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    provider_metadata: Optional[Dict[str, Any]]
    success: bool
    error: Optional[str]


@dataclass
class LCMSubmission:
    """Record of submission to CRT service."""
    operation: AgentOperation
    umf_body: Dict[str, Any]
    http_status: int
    middleware_response: Dict[str, Any]
    submission_latency_ms: float
    success: bool
    error: Optional[str]


class LiveAgentHarness:
    """Main harness for live agent evaluation."""
    
    def __init__(self, config_path: str = None, output_dir: str = None):
        self.provider_adapter = ProviderAdapter(config_path)
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent
        self.experiment_id = f"live_eval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Create output directories
        self.corpus_dir = self.output_dir / "_corpus"
        self.results_dir = self.output_dir
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        
        # CRT service configuration
        self.lcm_base_url = os.environ.get("LCM_SERVICE_URL", "http://127.0.0.1:8000")
        
        # Get available providers (avoid Ollama hanging)
        print("Checking provider availability...")
        self.available_providers = self.provider_adapter.get_available_models()
        print(f"Available providers: {self.available_providers}")
        
    def get_available_models(self) -> List[str]:
        """Get list of available LLM models."""
        # Use cached availability to avoid repeated Ollama checks
        return self.available_providers
    
    async def generate_agent_claims(
        self,
        scenario: str,
        agent_ids: List[str],
        providers: List[str],
        num_repetitions: int = 1,
        adversarial: bool = False
    ) -> List[AgentOperation]:
        """Generate claims from real LLM agents."""
        operations = []
        
        for rep in range(num_repetitions):
            for agent_id in agent_ids:
                for provider_key in providers:
                    if not self.provider_adapter.available_providers.get(provider_key):
                        print(f"Provider {provider_key} not available, skipping")
                        continue
                    
                    provider_config = self.provider_adapter.config["providers"][provider_key]
                    role = get_agent_role(agent_id)
                    
                    # Generate task-specific prompt
                    if adversarial:
                        prompt = get_adversarial_prompt(scenario, 
                            memory_path=f"test/{scenario}/path")
                    else:
                        prompt = get_full_prompt(agent_id, scenario, 
                            city=f"city_{rep}", 
                            location=f"loc_{rep}",
                            topic=f"topic_{rep}",
                            subject=f"subject_{rep}")
                    
                    # Create generation request
                    request = GenerationRequest(
                        provider=provider_config["provider"],
                        model=provider_config["model"],
                        agent_id=agent_id,
                        role=role.role_name,
                        prompt=prompt,
                        temperature=provider_config.get("temperature", 0.1),
                        max_tokens=provider_config.get("max_tokens", 512),
                        timeout=provider_config.get("timeout", 30)
                    )
                    
                    # Generate response
                    response = await self.provider_adapter.generate(request)
                    
                    # Parse response
                    parsed = self.provider_adapter.parse_json_response(response.raw_response)
                    
                    # Extract path and value from parsed response
                    target_path = "unknown/path"
                    claimed_value = response.raw_response
                    
                    if parsed and isinstance(parsed, dict):
                        target_path = parsed.get("path", target_path)
                        claimed_value = parsed.get("value", claimed_value)
                    
                    # Create operation record
                    operation = AgentOperation(
                        experiment_id=self.experiment_id,
                        run_id=f"{self.experiment_id}_rep{rep}",
                        provider=provider_config["provider"],
                        model=provider_config["model"],
                        agent_id=agent_id,
                        role=role.role_name,
                        prompt_hash=response.prompt_hash,
                        prompt_text=prompt,
                        raw_response=response.raw_response,
                        parsed_response=parsed,
                        target_path=target_path,
                        claimed_value=claimed_value,
                        timestamp=response.timestamp,
                        generation_latency_ms=response.latency_ms,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        provider_metadata=response.provider_metadata,
                        success=response.success,
                        error=response.error
                    )
                    
                    operations.append(operation)
        
        return operations
    
    def create_umf_body(self, operation: AgentOperation) -> Dict[str, Any]:
        """Create UMF body for CRT submission from agent operation."""
        return {
            "agent_id": operation.agent_id,
            "session_id": f"{operation.run_id}_{operation.agent_id}",
            "timestamp": operation.timestamp,
            "confidence_score": 0.5,  # Agent-reported confidence (for audit only)
            "assertion_payload": {
                operation.target_path: operation.claimed_value
            }
        }
    
    async def submit_to_lcm(self, operation: AgentOperation) -> LCMSubmission:
        """Submit agent operation to CRT service."""
        import httpx
        
        umf_body = self.create_umf_body(operation)
        
        start_time = datetime.utcnow()
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.lcm_base_url}/write",
                    json=umf_body,
                    timeout=30
                )
                response.raise_for_status()
                middleware_response = response.json()
            
            submission_latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            return LCMSubmission(
                operation=operation,
                umf_body=umf_body,
                http_status=response.status_code,
                middleware_response=middleware_response,
                submission_latency_ms=submission_latency_ms,
                success=True,
                error=None
            )
            
        except Exception as e:
            submission_latency_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            return LCMSubmission(
                operation=operation,
                umf_body=umf_body,
                http_status=0,
                middleware_response={},
                submission_latency_ms=submission_latency_ms,
                success=False,
                error=str(e)
            )
    
    async def run_stage1_live_experiment(
        self,
        scenario: str,
        agent_ids: List[str],
        providers: List[str],
        num_repetitions: int = 3
    ) -> Dict[str, Any]:
        """Run Stage 1 live agent experiment."""
        print(f"Running Stage 1 live experiment: {scenario}")
        print(f"Agents: {agent_ids}")
        print(f"Providers: {providers}")
        print(f"Repetitions: {num_repetitions}")
        
        # Phase A: Generate agent claims
        print("Phase A: Generating agent claims...")
        operations = await self.generate_agent_claims(
            scenario, agent_ids, providers, num_repetitions
        )
        
        print(f"Generated {len(operations)} agent operations")
        
        # Phase B: Submit to CRT
        print("Phase B: Submitting to CRT service...")
        submissions = []
        
        for operation in operations:
            submission = await self.submit_to_lcm(operation)
            submissions.append(submission)
        
        # Calculate metrics
        metrics = self.calculate_stage1_metrics(operations, submissions)
        
        # Save results
        results = {
            "experiment_id": self.experiment_id,
            "scenario": scenario,
            "timestamp": datetime.utcnow().isoformat(),
            "agents": agent_ids,
            "providers": providers,
            "num_repetitions": num_repetitions,
            "operations_count": len(operations),
            "submissions_count": len(submissions),
            "metrics": metrics,
            "operations": [asdict(op) for op in operations],
            "submissions": [asdict(sub) for sub in submissions]
        }
        
        return results
    
    def calculate_stage1_metrics(
        self, 
        operations: List[AgentOperation], 
        submissions: List[LCMSubmission]
    ) -> Dict[str, Any]:
        """Calculate Stage 1 evaluation metrics."""
        
        # Generation metrics
        total_operations = len(operations)
        successful_generations = sum(1 for op in operations if op.success)
        generation_success_rate = successful_generations / total_operations if total_operations > 0 else 0
        
        successful_parses = sum(1 for op in operations if op.parsed_response is not None)
        parse_success_rate = successful_parses / total_operations if total_operations > 0 else 0
        
        # Submission metrics
        total_submissions = len(submissions)
        successful_submissions = sum(1 for sub in submissions if sub.success)
        submission_success_rate = successful_submissions / total_submissions if total_submissions > 0 else 0
        
        # HTTP status metrics
        http_201_count = sum(1 for sub in submissions if sub.http_status == 201)
        http_201_rate = http_201_count / total_submissions if total_submissions > 0 else 0
        
        http_4xx_count = sum(1 for sub in submissions if 400 <= sub.http_status < 500)
        http_4xx_rate = http_4xx_count / total_submissions if total_submissions > 0 else 0
        
        http_5xx_count = sum(1 for sub in submissions if 500 <= sub.http_status < 600)
        http_5xx_rate = http_5xx_count / total_submissions if total_submissions > 0 else 0
        
        # Latency metrics
        generation_latencies = [op.generation_latency_ms for op in operations if op.success]
        submission_latencies = [sub.submission_latency_ms for sub in submissions if sub.success]
        
        import statistics
        generation_latency_stats = {
            "mean_ms": statistics.mean(generation_latencies) if generation_latencies else 0,
            "median_ms": statistics.median(generation_latencies) if generation_latencies else 0,
            "p50_ms": statistics.median(generation_latencies) if generation_latencies else 0,
            "p95_ms": sorted(generation_latencies)[int(len(generation_latencies) * 0.95)] if generation_latencies else 0,
            "min_ms": min(generation_latencies) if generation_latencies else 0,
            "max_ms": max(generation_latencies) if generation_latencies else 0
        } if generation_latencies else {}
        
        submission_latency_stats = {
            "mean_ms": statistics.mean(submission_latencies) if submission_latencies else 0,
            "median_ms": statistics.median(submission_latencies) if submission_latencies else 0,
            "p50_ms": statistics.median(submission_latencies) if submission_latencies else 0,
            "p95_ms": sorted(submission_latencies)[int(len(submission_latencies) * 0.95)] if submission_latencies else 0,
            "min_ms": min(submission_latencies) if submission_latencies else 0,
            "max_ms": max(submission_latencies) if submission_latencies else 0
        } if submission_latencies else {}
        
        return {
            "generation_success_rate": {
                "numerator": successful_generations,
                "denominator": total_operations,
                "percentage": generation_success_rate * 100
            },
            "parse_success_rate": {
                "numerator": successful_parses,
                "denominator": total_operations,
                "percentage": parse_success_rate * 100
            },
            "submission_success_rate": {
                "numerator": successful_submissions,
                "denominator": total_submissions,
                "percentage": submission_success_rate * 100
            },
            "http_201_rate": {
                "numerator": http_201_count,
                "denominator": total_submissions,
                "percentage": http_201_rate * 100
            },
            "http_4xx_rate": {
                "numerator": http_4xx_count,
                "denominator": total_submissions,
                "percentage": http_4xx_rate * 100
            },
            "http_5xx_rate": {
                "numerator": http_5xx_count,
                "denominator": total_submissions,
                "percentage": http_5xx_rate * 100
            },
            "generation_latency_stats": generation_latency_stats,
            "submission_latency_stats": submission_latency_stats
        }
    
    def save_results(self, results: Dict[str, Any], filename: str):
        """Save results to JSON file."""
        output_path = self.results_dir / filename
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to {output_path}")
    
    def save_corpus(self, operations: List[AgentOperation], filename: str):
        """Save frozen corpus for replay."""
        corpus_path = self.corpus_dir / filename
        with open(corpus_path, 'w') as f:
            for op in operations:
                f.write(json.dumps(asdict(op)) + '\n')
        print(f"Corpus saved to {corpus_path}")


async def main():
    """Main execution function."""
    harness = LiveAgentHarness()
    
    print("Available models:", harness.get_available_models())
    
    # Run Stage 1 experiments
    scenarios = ["weather_observation", "temperature_dispute"]
    agent_ids = ["agent_a", "agent_b", "agent_c", "agent_d"]
    
    # Use all available providers including OpenAI and Groq
    available_providers = harness.get_available_models()
    print(f"Using providers: {available_providers}")
    
    if not available_providers:
        print("ERROR: No providers available!")
        return
    
    # Focus on cloud providers first (OpenAI, Groq) for cross-provider heterogeneity
    cloud_providers = [p for p in available_providers if 'openai' in p or 'groq' in p]
    ollama_providers = [p for p in available_providers if 'ollama' in p]
    
    # Use cloud providers if available, otherwise use Ollama
    providers_to_use = cloud_providers if cloud_providers else ollama_providers
    
    # Limit to 2 providers for faster execution
    providers_to_use = providers_to_use[:2]
    
    print(f"Selected providers for execution: {providers_to_use}")
    
    for scenario in scenarios:
        print(f"\n{'='*60}")
        print(f"Running scenario: {scenario}")
        print(f"{'='*60}")
        
        results = await harness.run_stage1_live_experiment(
            scenario=scenario,
            agent_ids=agent_ids,
            providers=providers_to_use,
            num_repetitions=2
        )
        
        # Save results
        filename = f"06_STAGE1_{scenario.upper()}_RESULTS.json"
        harness.save_results(results, filename)
        
        # Save corpus
        operations = [AgentOperation(**op) for op in results["operations"]]
        corpus_filename = f"{scenario}_corpus.jsonl"
        harness.save_corpus(operations, corpus_filename)


if __name__ == "__main__":
    asyncio.run(main())