#!/usr/bin/env python3
"""验证脚本：检查 progress 中记录的推广是否真正修改为定向人群+完整标签。"""
import json, sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
from browser import Browser, load_json, ROOT

settings = load_json("settings.json")
sel = load_json("selectors.json")
at = sel["audience_targeting"]

TARGET_TAGS = ["14-24岁", "25~29岁", "30~34岁", "男", "女", "轰趴", "密室", "团建拓展", "新奇体验"]

progress = json.loads((ROOT / "data" / "batch_target_audience_progress.json").read_text(encoding="utf-8"))
pids = progress["processed_ids"]
print(f"待验证 {len(pids)} 条: {pids}")

def wait_table(b, timeout_s=10):
    d = time.time() + timeout_s
    while time.time() < d:
        for f in b.page.frames:
            if "promo-list" in (f.url or "") and f.locator(at["promotion_row"]).count() > 0:
                return f
        time.sleep(0.3)
    return None

def goto_edit(b, lid):
    url = (f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html"
           f"?planId={at['edit_plan_id']}&launchId={lid}&shopId={at['edit_shop_id']}"
           f"&brandId=0&isStatic=false&editCreative=false")
    b.page.goto(url, wait_until="domcontentloaded")
    d = time.time() + 10
    while time.time() < d:
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                time.sleep(1.2)
                return f
        time.sleep(0.3)
    return None

with Browser(settings) as b:
    # 进入列表，建立 pid -> launchId 映射（扫前 3 页）
    lf = wait_table(b)
    if not lf:
        b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html", wait_until="domcontentloaded")
        time.sleep(3)
        lf = wait_table(b)
    if not lf:
        print("列表未加载"); sys.exit(1)
    pid2lid = {}
    rows = lf.locator(at["promotion_row"]).all()
    for r in rows:
        try:
            pid = r.locator(at["promotion_id_link"]).first.inner_text().strip()
            lid = r.get_attribute("data-row-key")
            pid2lid[pid] = lid
        except Exception:
            pass
    print(f"列表扫描到 {len(pid2lid)} 条映射")

    results = []
    for pid in pids:
        lid = pid2lid.get(pid)
        if not lid:
            results.append((pid, "列表未找到", ""))
            continue
        ef = goto_edit(b, lid)
        if not ef:
            results.append((pid, "未进入编辑页", ""))
            continue
        item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
        try:
            item.scroll_into_view_if_needed(); time.sleep(0.8)
            st = item.evaluate("""el => {
                const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
                const r1 = radios.find(r => String(r.value) === '1');
                const detail = (el.querySelector('.static-target-detail-wrapper') || {innerText:''}).innerText || '';
                return {targeted: r1 ? r1.checked : false, detail};
            }""")
            detail = st["detail"]
            missing = [t for t in TARGET_TAGS if t not in detail]
            if st["targeted"] and not missing:
                results.append((pid, "OK 定向+9标签完整", ""))
            elif st["targeted"]:
                results.append((pid, f"定向但缺标签: {missing}", ""))
            else:
                results.append((pid, "未定向(智选)", ""))
        except Exception as e:
            results.append((pid, f"检查失败: {str(e)[:40]}", ""))

    print("\n===== 验证结果 =====")
    ok = 0
    for pid, status, _ in results:
        print(f"  {pid}: {status}")
        if status.startswith("OK"):
            ok += 1
    print(f"\n真正修改成功: {ok}/{len(results)}")
