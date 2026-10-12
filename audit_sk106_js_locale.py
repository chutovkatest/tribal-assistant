from pathlib import Path
import ast,json,subprocess,tempfile
root=Path("/home/worker/tribal-sk106-lab/tribal_assistant/core/game")
snips={}
for p in (root/"actions.py",root/"modules/game_sync.py"):
    for node in ast.parse(p.read_text()).body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            for target in node.targets:
                if isinstance(target,ast.Name) and (target.id.endswith("_JS") or target.id in ("REPORTS_JS","REPORT_DETAIL_JS","MENTORS_JS")):
                    snips[f"{p.name}:{target.id}"]=node.value.value
js="const d=JSON.parse(process.argv[1]);let e=[];for(const [name,code] of Object.entries(d)){try{new Function('return ('+code+')')}catch(x){e.push(name+': '+x.message)}}console.log('JS_SNIPPETS',Object.keys(d).length,'FAIL',e.length);for(const x of e)console.log(x);if(e.length)process.exit(1)"
r=subprocess.run(["node","-e",js,json.dumps(snips,ensure_ascii=False)],capture_output=True,text=True)
print(r.stdout,r.stderr)
raise SystemExit(r.returncode)
