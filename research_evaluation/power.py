from __future__ import annotations
import math


def power_analysis(pilot: dict | None, desired_power=.8, alpha=.05,
                   intracluster_correlation=0.0, mean_cluster_size=1.0):
    if not pilot or not pilot.get("independently_annotated"):
        return {"state":"blocked","message":"BLOCKED — no independently annotated pilot results available."}
    baseline, lcm = pilot.get("baseline_error"), pilot.get("lcm_error")
    if baseline is None or lcm is None: raise ValueError("pilot errors are required; values are never invented")
    effect=abs(baseline-lcm)
    if effect == 0: return {"state":"blocked","message":"BLOCKED — pilot absolute effect is zero."}
    z_alpha=1.959963984540054; z_power={.8:.8416212335729143,.9:1.2815515655446004}.get(desired_power)
    if z_power is None: raise ValueError("supported desired_power values are 0.8 and 0.9")
    pooled=(baseline+lcm)/2
    n=((z_alpha*math.sqrt(2*pooled*(1-pooled))+z_power*math.sqrt(baseline*(1-baseline)+lcm*(1-lcm)))**2)/(effect**2)
    design=1+(mean_cluster_size-1)*intracluster_correlation
    sensitivity={str(round(effect*f,6)):math.ceil(n/(f*f)*design) for f in (.5,.75,1,1.25,1.5)}
    return {"state":"completed","assumed_baseline_error":baseline,"assumed_lcm_error":lcm,"absolute_effect":effect,"desired_power":desired_power,"alpha":alpha,"cluster_assumptions":{"intracluster_correlation":intracluster_correlation,"mean_cluster_size":mean_cluster_size,"design_effect":design},"estimated_required_independent_cases":math.ceil(n*design),"sensitivity":sensitivity}
