"""Post-edit integrity check: syntax + 'self.<attr>' calls that don't exist.

get_errors has lied twice in this repo (reported clean while the file had a hard
syntax error and calls to non-existent methods), so this is run after EVERY edit.
"""
import ast
import builtins
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["auto_trader.py", "position_monitor.py", "app.py", "strategy_learner.py"]

BUILTIN_ATTRS = set(dir(builtins))


def check(path):
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        print(f"  SYNTAX ERROR {path}:{e.lineno}: {e.msg}")
        return 1

    problems = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        defined = set()
        for n in ast.walk(cls):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)                 # x = ...
                    elif (isinstance(t, ast.Attribute)
                          and isinstance(t.value, ast.Name)
                          and t.value.id == "self"):
                        defined.add(t.attr)               # self.x = ...
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                defined.add(n.target.id)

        used = set()
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self"):
                used.add(n.attr)

        # Slots come from the shared BaseStrategy-style base via __getattr__,
        # so only report attributes that look like real method calls.
        unknown = sorted(a for a in used - defined if not a.startswith("__"))
        if unknown:
            problems.append((cls.name, unknown))

    if problems:
        for cname, attrs in problems:
            print(f"  {path}: class {cname} references undefined self.*: {attrs}")
        return len(problems)
    print(f"  OK  {path}  ({len(src.splitlines())} lines)")
    return 0


bad = 0
for f in FILES:
    p = os.path.join(HERE, f)
    if not os.path.exists(p):
        print(f"  --  {f} missing (skipped)")
        continue
    bad += check(p)

print("\nRESULT:", "FAIL" if bad else "all files parse and class attrs resolve")
sys.exit(1 if bad else 0)
