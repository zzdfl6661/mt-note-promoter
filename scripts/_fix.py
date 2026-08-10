import re, pathlib
p = pathlib.Path("D:/Version1/mt-note-promoter/scripts/batch_target_audience.py")
s = p.read_text(encoding="utf-8")
old = """        list_frame.evaluate(f\"\"\"() => {{
            const opts = [...document.querySelectorAll('li')].filter(l => l.innerText.trim() === '{value}');
            if (opts[0]) opts[0].click();
        }}\"\"\")"""
new = """        js = \"\"\"v => {
            const opts = [...document.querySelectorAll('li')].filter(l => l.innerText.trim() === v);
            if (opts[0]) opts[0].click();
        }\"\"\"
        list_frame.evaluate(js, value)"""
assert old in s, "not found"
p.write_text(s.replace(old, new), encoding="utf-8")
print("OK")