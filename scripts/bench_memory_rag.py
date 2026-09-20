"""Deterministic RAG benchmark with latency, RSS and SQLite file metrics."""
from __future__ import annotations
import argparse, hashlib, json, os, platform, resource, sqlite3, statistics, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def percentile(values, p):
    if not values: return None
    xs=sorted(values); pos=(len(xs)-1)*p; lo=int(pos); hi=min(lo+1,len(xs)-1); return xs[lo]+(xs[hi]-xs[lo])*(pos-lo)
def stats(values):
    return {f"p{int(p*100)}_s": percentile(values,p) for p in (0.5,0.95,0.99)}
def rss_bytes():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value*1024 if sys.platform.startswith("linux") else value)
def file_sizes(db):
    paths={"db":db,"wal":db.with_name(db.name+"-wal"),"shm":db.with_name(db.name+"-shm")}
    return {k:(p.stat().st_size if p.exists() else 0) for k,p in paths.items()}
def corpus_fixture(n, chars):
    docs=[]
    for i in range(n):
        text=(f"# Project {i}\n\n## Overview\nDeterministic entity entity-{i:05d} supports alpha systems.\n\n"
              f"## Operations\nThe indexed record contains query-{i%11}, region-{i%7}, and stable reference {i}.\n\n"
              "## Notes\n"+("Reusable boilerplate with varied Markdown sections.\n"*((chars//55)+1)))[:chars]
        docs.append((f"doc-{i:05d}",text))
    return docs
def atomic_write(path, payload):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.replace(tmp,path)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--docs",type=int,default=100); ap.add_argument("--chars",type=int,default=4000); ap.add_argument("--repetitions",type=int,default=20); ap.add_argument("--workers",type=int,default=4); ap.add_argument("--db",type=Path); ap.add_argument("--json",type=Path); a=ap.parse_args()
    if min(a.docs,a.chars,a.repetitions,a.workers)<1: ap.error("docs/chars/repetitions/workers must be positive")
    from hermes.platform.memory.ragflow_engine import RAGFlowStore
    with tempfile.TemporaryDirectory() as td:
        db=a.db or Path(td)/"rag.db"; corpus=corpus_fixture(a.docs,a.chars); manifest={"version":1,"docs":[{"id":i,"chars":len(t),"sha256":hashlib.sha256(t.encode()).hexdigest()} for i,t in corpus]}; manifest["sha256"]=hashlib.sha256(json.dumps(manifest["docs"],sort_keys=True).encode()).hexdigest(); base_rss=rss_bytes(); store=RAGFlowStore(db); init_rss=rss_bytes()
        indexing=[]
        for doc_id,text in corpus:
            s=time.perf_counter(); store.index_document(doc_id,text,doc_id=doc_id); indexing.append(time.perf_counter()-s)
        after_index=rss_bytes(); queries=["entity-00001 alpha", "query-3 region-2", "no-such-token"]
        for q in queries: store.hybrid_search(q)
        searches=[]
        for i in range(a.repetitions): s=time.perf_counter(); store.hybrid_search(queries[i%len(queries)]); searches.append(time.perf_counter()-s)
        def one(i): s=time.perf_counter(); store.hybrid_search(queries[i%len(queries)]); return time.perf_counter()-s
        s=time.perf_counter()
        with ThreadPoolExecutor(max_workers=a.workers) as ex: concurrent=list(ex.map(one,range(a.repetitions)))
        wall=time.perf_counter()-s; files=file_sizes(db); result={"schema_version":1,"environment":{"python":platform.python_version(),"sqlite":sqlite3.sqlite_version,"platform":platform.platform(),"rss_metric":"ru_maxrss normalized to bytes"},"config":{"docs":a.docs,"chars":a.chars,"repetitions":a.repetitions,"workers":a.workers,"db":str(db)},"corpus":manifest,"rss_bytes":{"baseline":base_rss,"after_init":init_rss,"after_index":after_index,"peak":rss_bytes()},"indexing":{"docs":a.docs,**stats(indexing)},"search":stats(searches),"concurrency":{"workers":a.workers,"ops":len(concurrent),"wall_s":wall,"throughput":len(concurrent)/wall,**stats(concurrent)},"sqlite_files":files}
        print(json.dumps(result,indent=2,sort_keys=True));
        if a.json: atomic_write(a.json,result)
        del store
if __name__=="__main__": main()
