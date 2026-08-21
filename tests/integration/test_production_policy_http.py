import os,socket,subprocess,sys,tempfile,time
from datetime import datetime
from pathlib import Path
import httpx,pytest
from crt_core.confidence_engine import EvidenceType
from crt_core.crypto import sign_assertion_evidence

ROOT=Path(__file__).resolve().parents[2]
def port():
 s=socket.socket();s.bind(("127.0.0.1",0));p=s.getsockname()[1];s.close();return p
def start(policy,db,p):
 env=os.environ.copy();env.update({"CRT_SQLITE_PATH":db,"CRT_ALLOW_DEV_EVIDENCE_KEY":"1","CRT_RESOLUTION_POLICY":policy,"CRT_EVALUATION_MODE":"1"});proc=subprocess.Popen([sys.executable,"-m","uvicorn","crt_service.app:app","--host","127.0.0.1","--port",str(p),"--log-level","warning"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 for _ in range(100):
  try:
   if httpx.get(f"http://127.0.0.1:{p}/",timeout=.5).json().get("resolution_policy")==policy:return proc
  except Exception:time.sleep(.05)
 proc.kill();raise RuntimeError("service failed")
def stop(p):
 p.terminate()
 try:p.wait(5)
 except subprocess.TimeoutExpired:p.kill();p.wait()
def payload(agent,path,value,ts,etype,source,nonce,issued=None,expires=None):
 sig=sign_assertion_evidence(EvidenceType(etype),source,agent_id=agent,timestamp=ts,assertion_payload={path:value},domain="controlled",nonce=nonce,issued_at=issued,expires_at=expires)
 return {"agent_id":agent,"session_id":"policy-conformance","timestamp":ts,"confidence_score":.5,"assertion_payload":{path:value},"domain":"controlled","evidence_records":[{"type":etype,"source":source,"relevance":1,"verified":True,"nonce":nonce,"issued_at":issued,"expires_at":expires}],"evidence_signature":sig}

@pytest.mark.parametrize("policy",["last_write_wins","recency_only","full_crt"])
def test_policy_real_http_security_conflict_and_restart(policy):
 with tempfile.TemporaryDirectory() as td:
  db=str(Path(td)/"x.sqlite");p=port();proc=start(policy,db,p);base=f"http://127.0.0.1:{p}"
  try:
   a=payload("a","policy.clear","OLD","2026-08-08T10:00:00","document","a",f"{policy}-a");b=payload("b","policy.clear","NEW","2026-08-08T17:00:00","database","b",f"{policy}-b")
   assert httpx.post(base+"/write",json=a).status_code==201;rb=httpx.post(base+"/write",json=b);assert rb.status_code==201
   was_unresolved=rb.json().get("status")=="unresolved"
   expected="NEW"
   assert httpx.get(base+"/context/policy.clear").json()["facts"][0]["assertion_payload"]["policy.clear"]==expected
   bad=payload("bad","policy.bad","X","2026-08-08T17:01:00","database","bad",f"{policy}-bad");bad["assertion_payload"]={"policy.bad":"ALTERED"};assert httpx.post(base+"/write",json=bad).status_code==400
   replay=payload("r","policy.replay","R","2026-08-08T17:02:00","tool_output","r",f"{policy}-replay");assert httpx.post(base+"/write",json=replay).status_code==201;assert httpx.post(base+"/write",json=replay).status_code==400
   expired=payload("e","policy.expired","E","2026-08-08T17:03:00","database","e",f"{policy}-expired","2026-08-01T00:00:00","2026-08-02T00:00:00");assert httpx.post(base+"/write",json=expired).status_code==400
   tie1=payload("t1","policy.tie","X","2026-08-08T17:04:00","tool_output","t1",f"{policy}-t1");tie2=payload("t2","policy.tie","Y","2026-08-08T17:04:00","tool_output","t2",f"{policy}-t2");assert httpx.post(base+"/write",json=tie1).status_code==201;rt=httpx.post(base+"/write",json=tie2);assert rt.status_code==201;assert (rt.json()["status"]=="conflict_resolved")==(policy=="last_write_wins")
  finally:stop(proc)
  proc=start(policy,db,p)
  try:assert httpx.get(base+"/context/policy.clear").json()["count"]==(2 if was_unresolved else 1)
  finally:stop(proc)
