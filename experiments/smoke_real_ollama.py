"""
Smoke Test Helper for Real Ollama Agent Experiments

This script verifies that the real agent experiment infrastructure is working correctly
with Ollama and the LCM service. It's designed to be run manually or in CI to validate
the fail-closed behavior and honest reporting of agent mode.

REQUIREMENTS:
- Ollama service running with llama3.1:8b model available
- LCM service running on http://localhost:8000 (uvicorn lcm_service.app:app)

USAGE:
    python experiments/smoke_real_ollama.py

EXPECTED BEHAVIOR:
- Passes if: agent_mode == "real_llm", model contains "llama3.1:8b", no "Simulated" in logs
- Fails if: LLM unavailable (non-zero exit)
"""

import sys
import json
import subprocess
from pathlib import Path


def check_ollama_available():
    """Check if Ollama is running and llama3.1:8b is available."""
    try:
        import requests
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        
        if "llama3.1:8b" in model_names:
            print("✓ Ollama running with llama3.1:8b available")
            return True
        else:
            print(f"✗ Ollama running but llama3.1:8b not found. Available: {model_names}")
            return False
    except Exception as e:
        print(f"✗ Ollama not available: {e}")
        return False


def check_lcm_service_available():
    """Check if LCM service is running on localhost:8000."""
    try:
        import requests
        response = requests.get("http://localhost:8000/health", timeout=5)
        response.raise_for_status()
        print("✓ LCM service running on http://localhost:8000")
        return True
    except Exception as e:
        print(f"✗ LCM service not available: {e}")
        return False


def run_smoke_test():
    """Run the smoke test with Ollama backend."""
    print("=" * 70)
    print("SMOKE TEST: Real Ollama Agent Experiment")
    print("=" * 70)
    
    # Check prerequisites
    ollama_ok = check_ollama_available()
    lcm_ok = check_lcm_service_available()
    
    if not ollama_ok or not lcm_ok:
        print("\n❌ Prerequisites not met. Please start required services:")
        if not ollama_ok:
            print("  - Start Ollama: ollama serve")
            print("  - Pull model: ollama pull llama3.1:8b")
        if not lcm_ok:
            print("  - Start LCM service: uvicorn lcm_service.app:app --reload --port 8000")
        return False
    
    print("\n" + "=" * 70)
    print("Running experiment with --backend ollama")
    print("=" * 70)
    
    # Run the unified experiment runner
    try:
        result = subprocess.run(
            [
                sys.executable,
                "experiments/run_real_agent_experiment.py",
                "--backend", "ollama",
                "--scenario", "basic",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        if result.returncode != 0:
            print(f"\n❌ Experiment failed with exit code {result.returncode}")
            return False
        
    except subprocess.TimeoutExpired:
        print("\n❌ Experiment timed out after 5 minutes")
        return False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return False
    
    # Parse and validate results
    print("\n" + "=" * 70)
    print("Validating results")
    print("=" * 70)
    
    # Find the most recent result file
    results_dir = Path("experiments/results")
    if not results_dir.exists():
        print("❌ Results directory not found")
        return False
    
    # Find the most recent unified_ollama_*.json file
    result_files = sorted(results_dir.glob("unified_ollama_*.json"), reverse=True)
    if not result_files:
        print("❌ No result file found")
        return False
    
    result_file = result_files[0]
    print(f"Reading results from: {result_file}")
    
    try:
        with open(result_file, "r") as f:
            results = json.load(f)
    except Exception as e:
        print(f"❌ Failed to read results: {e}")
        return False
    
    # Validate agent_mode
    agent_mode = results.get("agent_mode")
    if agent_mode != "real_llm":
        print(f"❌ Expected agent_mode='real_llm', got '{agent_mode}'")
        return False
    print(f"✓ agent_mode: {agent_mode}")
    
    # Validate model
    model = results.get("model", "")
    if "llama3.1:8b" not in model:
        print(f"❌ Expected model to contain 'llama3.1:8b', got '{model}'")
        return False
    print(f"✓ model: {model}")
    
    # Check for simulation markers in logs
    write_log = results.get("write_log", [])
    stdout = result.stdout.lower()
    
    if "[simulated]" in stdout or "[fallback]" in stdout:
        print("❌ Found simulation markers in output")
        return False
    print("✓ No simulation markers in output")
    
    # Validate stats are present
    stats = results.get("stats", {})
    required_stats = ["total_writes", "gate_rejected", "conflict_resolved", "tool_call_failed"]
    for stat in required_stats:
        if stat not in stats:
            print(f"❌ Missing required stat: {stat}")
            return False
    print(f"✓ All required stats present: {required_stats}")
    
    print("\n" + "=" * 70)
    print("✅ SMOKE TEST PASSED")
    print("=" * 70)
    print(f"Agent mode: {agent_mode}")
    print(f"Model: {model}")
    print(f"Total writes: {stats.get('total_writes')}")
    print(f"Tool call failed: {stats.get('tool_call_failed')}")
    print(f"Result file: {result_file}")
    
    return True


if __name__ == "__main__":
    success = run_smoke_test()
    sys.exit(0 if success else 1)
