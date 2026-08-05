import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from .pilot import blank_episode,import_source

def exclusive(path,value):
    if path.exists(): raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,indent=2)+"\n","utf-8")
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    source=sub.add_parser("import-source"); source.add_argument("path",type=Path); source.add_argument("--url",required=True); source.add_argument("--retrieved-at",required=True); source.add_argument("--out",type=Path,required=True)
    blank=sub.add_parser("blank-episode"); blank.add_argument("--id",required=True); blank.add_argument("--domain",required=True); blank.add_argument("--entity",nargs="+",required=True); blank.add_argument("--evaluation-time",required=True); blank.add_argument("--conflict-family",required=True); blank.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    if a.command=="import-source": value=import_source(a.path,a.url,datetime.fromisoformat(a.retrieved_at))
    else: value=blank_episode(a.id,a.domain,a.entity,datetime.fromisoformat(a.evaluation_time),a.conflict_family)
    exclusive(a.out,value)
if __name__=="__main__": main()
