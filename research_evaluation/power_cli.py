import argparse, json
from pathlib import Path
from .power import power_analysis

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("pilot", nargs="?", type=Path)
    parser.add_argument("--power", type=float, default=.8); parser.add_argument("--alpha", type=float, default=.05)
    parser.add_argument("--icc", type=float, default=0); parser.add_argument("--cluster-size", type=float, default=1)
    args=parser.parse_args(); pilot=json.loads(args.pilot.read_text("utf-8")) if args.pilot else None
    print(json.dumps(power_analysis(pilot,args.power,args.alpha,args.icc,args.cluster_size),indent=2))

if __name__ == "__main__": main()
