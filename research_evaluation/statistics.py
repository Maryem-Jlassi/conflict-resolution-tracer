from __future__ import annotations
import itertools, math, random
from collections import Counter, defaultdict
from typing import Callable


def _normal_two_sided(z): return math.erfc(abs(z)/math.sqrt(2))


def mcnemar(a, b, truth):
    discordant_a = sum(x == t and y != t for x,y,t in zip(a,b,truth))
    discordant_b = sum(x != t and y == t for x,y,t in zip(a,b,truth))
    n=discordant_a+discordant_b
    p=1.0 if n==0 else min(1.0, 2*sum(math.comb(n,k) for k in range(min(discordant_a,discordant_b)+1))/2**n)
    return {"a_only_correct":discordant_a,"b_only_correct":discordant_b,"statistic":abs(discordant_a-discordant_b),"p_value":p,"independent_cases":len(truth)}


def paired_permutation(differences, permutations=10000, seed=0):
    if not differences: raise ValueError("differences required")
    observed=abs(sum(differences)/len(differences)); rng=random.Random(seed); extreme=0
    for _ in range(permutations):
        value=abs(sum(x if rng.random()<.5 else -x for x in differences)/len(differences))
        extreme += value >= observed
    return {"mean_difference":sum(differences)/len(differences),"p_value":(extreme+1)/(permutations+1),"independent_cases":len(differences)}


def wilcoxon_signed_rank(differences):
    nonzero=[x for x in differences if x != 0]
    ranked=sorted(enumerate(nonzero), key=lambda pair:abs(pair[1])); ranks=[0.0]*len(nonzero)
    i=0
    while i<len(ranked):
        j=i
        while j+1<len(ranked) and abs(ranked[j+1][1])==abs(ranked[i][1]): j+=1
        rank=(i+j+2)/2
        for k in range(i,j+1): ranks[ranked[k][0]]=rank
        i=j+1
    pos=sum(r for r,x in zip(ranks,nonzero) if x>0); neg=sum(r for r,x in zip(ranks,nonzero) if x<0); n=len(nonzero)
    mean=n*(n+1)/4; variance=n*(n+1)*(2*n+1)/24
    return {"statistic":min(pos,neg),"p_value":1.0 if not n else _normal_two_sided((pos-mean)/math.sqrt(variance)),"independent_cases":len(differences)}


def clustered_bootstrap(rows, cluster_key, value_key, resamples=2000, seed=0, confidence=.95):
    clusters=defaultdict(list)
    for row in rows: clusters[row[cluster_key]].append(float(row[value_key]))
    keys=list(clusters); rng=random.Random(seed); estimates=[]
    for _ in range(resamples):
        sample=[value for _ in keys for value in clusters[rng.choice(keys)]]
        estimates.append(sum(sample)/len(sample))
    estimates.sort(); alpha=(1-confidence)/2
    return {"estimate":sum(float(r[value_key]) for r in rows)/len(rows),"confidence_interval":(estimates[int(alpha*resamples)],estimates[min(resamples-1,int((1-alpha)*resamples))]),"independent_cases":len(keys),"repeated_observations":len(rows)-len(keys)}


def effect_sizes(a,b):
    differences=[x-y for x,y in zip(a,b)]; mean=sum(differences)/len(differences)
    variance=sum((x-mean)**2 for x in differences)/(len(differences)-1) if len(differences)>1 else 0
    return {"mean_difference":mean,"paired_standardized_difference":mean/math.sqrt(variance) if variance else None}


def holm(p_values):
    ordered=sorted(enumerate(p_values), key=lambda x:x[1]); adjusted=[0.0]*len(p_values); running=0.0
    for rank,(index,p) in enumerate(ordered):
        running=max(running,min(1.0,(len(p_values)-rank)*p)); adjusted[index]=running
    return adjusted
