import json
from pathlib import Path
import pytest
from research_evaluation import protocol_cli as pc

def bundle(tmp_path,monkeypatch):
    files={}
    for name in pc.BUNDLE_FILES:
        path=tmp_path/("source-"+name); path.write_text(name,"utf-8"); files[name]=path
    monkeypatch.setattr(pc,"BUNDLE_FILES",files); return files

def test_protocol_freeze_and_no_unearned_supervisor_approval(tmp_path,monkeypatch):
    bundle(tmp_path,monkeypatch); out=tmp_path/"v1"
    manifest=pc.freeze_protocol("1.0","researcher",out)
    assert manifest["bundle_sha256"] and not manifest["supervisor_approved"]
    assert json.loads((out/"manifest.json").read_text())["approved_by"]=="researcher"

def test_frozen_protocol_immutable(tmp_path,monkeypatch):
    bundle(tmp_path,monkeypatch); out=tmp_path/"v1"; pc.freeze_protocol("1.0","person",out)
    with pytest.raises(FileExistsError): pc.freeze_protocol("1.0","person",out)

def test_amendment_record_complete_and_immutable(tmp_path):
    out=tmp_path/"a1.json"; record=pc.create_amendment(out,"A-001","clarify",["protocol.md"],False,True,"approver",{"protocol.md":"old"},{"protocol.md":"new"})
    assert record["amendment_id"]=="A-001" and record["affects_primary_analysis"]
    with pytest.raises(FileExistsError): pc.create_amendment(out,"A-001","x",[],False,False,"a",{}, {})
