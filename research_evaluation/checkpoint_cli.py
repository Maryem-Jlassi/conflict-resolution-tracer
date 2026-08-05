import argparse,json
from pathlib import Path
from .checkpoint import write_report
def main():
    p=argparse.ArgumentParser(); p.add_argument("--out",type=Path,required=True); a=p.parse_args()
    print(json.dumps(write_report(a.out),indent=2))
if __name__=="__main__": main()
