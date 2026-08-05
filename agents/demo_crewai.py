"""
CrewAI Integration Demo

Demonstrates a CrewAI agent using LCM via lcm_client SDK.
No direct communication with other agents - only through LCM.
"""

from lcm_client import LCMClient
from datetime import datetime
from typing import Dict, Any


class LCMTool:
    """
    Simulated CrewAI tool wrapper for LCM.
    
    In a real implementation, this would be a crewai.tools.BaseTool subclass.
    For Phase 10 demo purposes, we simulate the tool behavior.
    """
    
    def __init__(self, lcm_client: LCMClient, agent_id: str, session_id: str):
        self.lcm = lcm_client
        self.agent_id = agent_id
        self.session_id = session_id
    
    def write_memory(
        self,
        path: str,
        value: Any,
        confidence: float = 0.8
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
    
    def read_memory(self, path: str) -> Dict[str, Any]:
        """Read memory from LCM."""
        return self.lcm.get_context(path)


def run_crewai_demo(lcm_base_url: str = "http://localhost:8000"):
    """
    Run the CrewAI demo agent.
    
    Simulates a 'Triage Agent' from CrewAI making assertions about
    a patient's priority level.
    """
    print("\n=== CrewAI Triage Agent Demo ===")
    
    # Initialize LCM client
    lcm = LCMClient(base_url=lcm_base_url)
    
    # Create tool wrapper
    tool = LCMTool(
        lcm_client=lcm,
        agent_id="crewai_triage_agent",
        session_id="crew_session_001"
    )
    
    # Agent makes an assertion
    print("\n[CrewAI] Triage agent assessing patient priority...")
    result = tool.write_memory(
        path="patient.ehr_001.priority",
        value="low",
        confidence=0.85
    )
    
    print(f"[CrewAI] Write result: {result['status']}")
    print(f"[CrewAI] Provenance ID: {result['provenance_id']}")
    
    # Read back the memory
    context = tool.read_memory("patient.ehr_001.priority")
    print(f"[CrewAI] Current context: {context['facts'][0]['assertion_payload'] if context['facts'] else 'empty'}")
    
    return result


if __name__ == "__main__":
    # For standalone testing, assumes LCM server is running
    run_crewai_demo()
