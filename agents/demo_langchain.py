"""
LangChain Integration Demo

Demonstrates a LangChain agent using LCM via lcm_client SDK.
No direct communication with other agents - only through LCM.
"""

from lcm_client import LCMClient
from datetime import datetime
from typing import Dict, Any


class LCMLangChainTool:
    """
    Simulated LangChain tool wrapper for LCM.
    
    In a real implementation, this would use @tool decorator from langchain.
    For Phase 10 demo purposes, we simulate the tool behavior.
    """
    
    def __init__(self, lcm_client: LCMClient, agent_id: str, session_id: str):
        self.lcm = lcm_client
        self.agent_id = agent_id
        self.session_id = session_id
    
    def write_to_lcm(
        self,
        path: str,
        value: Any,
        confidence: float = 0.9
    ) -> Dict[str, Any]:
        """Write a memory assertion to LCM."""
        result = self.lcm.write(
            agent_id=self.agent_id,
            session_id=self.session_id,
            confidence_score=confidence,
            assertion_payload={path: value},
            timestamp=datetime.utcnow()
        )
        return result
    
    def query_lcm(self, path: str) -> Dict[str, Any]:
        """Query memory from LCM."""
        return self.lcm.get_context(path)


def run_langchain_demo(
    lcm_base_url: str = "http://localhost:8000",
    timestamp_offset_seconds: int = 0
):
    """
    Run the LangChain demo agent.
    
    Simulates an 'EHR Lookup Agent' from LangChain making assertions
    about the same patient, potentially conflicting with CrewAI.
    
    Args:
        lcm_base_url: Base URL of LCM service
        timestamp_offset_seconds: Offset to simulate older/newer assertions
    """
    print("\n=== LangChain EHR Agent Demo ===")
    
    # Initialize LCM client
    lcm = LCMClient(base_url=lcm_base_url)
    
    # Create tool wrapper
    tool = LCMLangChainTool(
        lcm_client=lcm,
        agent_id="langchain_ehr_agent",
        session_id="lang_session_001"
    )
    
    # Agent makes an assertion (potentially conflicting)
    print("\n[LangChain] EHR agent reviewing patient records...")
    
    # Simulate timestamp offset for testing
    from datetime import timedelta
    timestamp = datetime.utcnow() + timedelta(seconds=timestamp_offset_seconds)
    
    result = tool.write_to_lcm(
        path="patient.ehr_001.priority",
        value="high",
        confidence=0.92
    )
    
    print(f"[LangChain] Write result: {result['status']}")
    print(f"[LangChain] Provenance ID: {result['provenance_id']}")
    
    if result.get("winner_agent"):
        print(f"[LangChain] Conflict detected!")
        print(f"[LangChain] Winner: {result['winner_agent']}")
        print(f"[LangChain] Loser: {result['loser_agent']}")
        print(f"[LangChain] Reason: {result['message']}")
    
    # Read back the memory
    context = tool.query_lcm("patient.ehr_001.priority")
    if context['facts']:
        winning_fact = context['facts'][0]
        print(f"\n[LangChain] Final committed value: {winning_fact['assertion_payload']}")
        print(f"[LangChain] Committed by agent: {winning_fact['agent_id']}")
        print(f"[LangChain] Confidence: {winning_fact['confidence_score']}")
    
    return result


if __name__ == "__main__":
    # For standalone testing, assumes LCM server is running
    run_langchain_demo()
