from __future__ import annotations

import asyncio
import json
from typing import Any


SAVE_DRAFT_SCRIPT = r"""
(async () => {
  const clean = value => String(value || '').replace(/\s+/g, '').replace(/[!！。:：]/g, '');
  const labels = new Set(['暂时保存', '暂存', '保存草稿', '临时保存', '保存进度']);
  const visible = node => node && node.getClientRects().length > 0
    && getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none';
  const button = [...document.querySelectorAll('a,button,input[type=button],input[type=submit],[role=button]')]
    .find(node => visible(node) && labels.has(clean(node.innerText || node.value || node.textContent))
      && !node.closest('.questionLi,.edui-editor-toolbarbox') && !node.disabled
      && node.getAttribute('aria-disabled') !== 'true');
  if (!button) return JSON.stringify({saved: false, reason: '未找到可用的暂时保存按钮'});
  const feedbackSelector = '#dialogToast,.toast,[role=status],[role=alert],.layui-layer-content,.el-message__content,.ant-message-notice-content';
  const feedback = () => [...document.querySelectorAll(feedbackSelector)]
    .filter(visible).map(node => (node.innerText || node.textContent || '').trim());
  const previous = new Set(feedback());
  const deadline = Date.now() + 15000;
  while (window.saveLock === true && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 100));
  if (window.saveLock === true) return JSON.stringify({saved: false, reason: '页面已有保存请求未结束，本次没有重复点击'});
  let finish;
  const result = new Promise(resolve => { finish = resolve; });
  let settled = false;
  const done = value => { if (!settled) { settled = true; finish(value); } };
  const original = window.saveWork;
  const nativeSave = typeof original === 'function' && /\bsaveWork\s*\(/.test(button.getAttribute('onclick') || '');
  if (nativeSave) {
    window.saveWork = function(options) {
      const supplied = options || {};
      return original.call(this, {...supplied,
        success(data) {
          done({saved: true, evidence: 'platform_callback', message: '平台已确认暂时保存成功'});
          if (typeof supplied.success === 'function') supplied.success(data);
        },
        failure(data) {
          done({saved: false, reason: String(data?.msg || '平台返回暂时保存失败')});
          if (typeof supplied.failure === 'function') supplied.failure(data);
        }
      });
    };
  }
  try {
    button.click();
  } catch (error) {
    done({saved: false, reason: String(error?.message || error)});
  } finally {
    if (nativeSave) window.saveWork = original;
  }
  const timer = setInterval(() => {
    for (const message of feedback()) {
      if (previous.has(message)) continue;
      if (/保存失败|暂存失败|保存出错|网络异常|登录失效|请重新登录/.test(message)) done({saved: false, reason: message});
      else if (/保存成功|暂存成功|草稿已保存|进度已保存/.test(message)) done({saved: true, evidence: 'page_feedback', message});
    }
    if (Date.now() >= deadline) done({saved: false, reason: '已点击暂时保存，但未收到平台成功反馈，请在原浏览器核对'});
  }, 100);
  try { return JSON.stringify(await result); }
  finally { clearInterval(timer); }
})
"""


class DraftSaveError(RuntimeError):
    pass


async def save_assignment_draft(session: Any) -> dict[str, Any]:
    try:
        page = await session.get_current_page()
        if page is None:
            raise DraftSaveError("没有可暂存作业的浏览器页面")
        raw = await asyncio.wait_for(page.evaluate(SAVE_DRAFT_SCRIPT), timeout=20)
        result = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as error:
        raise DraftSaveError(f"暂时保存未能确认成功：{error}") from error
    if not isinstance(result, dict) or result.get("saved") is not True:
        reason = result.get("reason") if isinstance(result, dict) else "页面没有返回保存结果"
        raise DraftSaveError(str(reason or "平台没有确认暂存成功"))
    return result
