import json, subprocess, sys
PKGS = ["react","react-dom","vite","tailwindcss","@tailwindcss/vite","lucide-react","motion","framer-motion","echarts","echarts-for-react","uplot","react-router-dom","react-router","@vitejs/plugin-react-swc","@radix-ui/react-progress","@radix-ui/react-slot","class-variance-authority","clsx","tailwind-merge"]
def load(ref, path):
    if ref == "WORKTREE":
        return open(path).read()
    return subprocess.check_output(["git","show",f"{ref}:{path}"], text=True)
for ref in ["e95f974d","2a657fad","HEAD","WORKTREE"]:
    pj = json.loads(load(ref, "frontend/package.json"))
    lk = json.loads(load(ref, "frontend/package-lock.json"))
    print(f"=== {ref}  lockfileVersion={lk.get('lockfileVersion')}")
    deps = {**pj.get("dependencies",{}), **pj.get("devDependencies",{})}
    for p in PKGS:
        spec = deps.get(p, "-")
        node = lk.get("packages",{}).get(f"node_modules/{p}")
        res = node.get("version") if node else "-"
        print(f"  {p:28s} spec={spec:12s} locked={res}")
