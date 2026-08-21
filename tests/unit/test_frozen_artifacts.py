import hashlib,json
from pathlib import Path
import pytest
from research_evaluation.frozen_artifacts import immutable_json_write,validate_final_authorization


def test_final_authorization_binds_token_and_split(tmp_path:Path):
    split=tmp_path/'final_split_manifest.json'; split.write_text('{}\n')
    token='reviewed-secret'; record={"status":"approved","authorized_by":"supervisor","authorization_id":"a1",
      "authorization_token_sha256":hashlib.sha256(token.encode()).hexdigest(),
      "split_manifest_sha256":hashlib.sha256(split.read_bytes()).hexdigest()}
    validate_final_authorization(record,token=token,split_manifest=split)
    with pytest.raises(PermissionError): validate_final_authorization(record,token='wrong',split_manifest=split)
    split.write_text('{"changed":true}\n')
    with pytest.raises(PermissionError): validate_final_authorization(record,token=token,split_manifest=split)


def test_immutable_writer_refuses_overwrite(tmp_path:Path):
    path=tmp_path/'artifact.json'; immutable_json_write(path,{"x":1})
    with pytest.raises(FileExistsError): immutable_json_write(path,{"x":2})
