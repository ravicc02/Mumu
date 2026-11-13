/* Mumu native course playback and resource runtime. */
(() => {
  if (window.__studioLearning?.version === 'mumu-learning-runtime-v1') return;
  const visible = node => Boolean(node?.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
  let generation = 0;
  const waits = new Set();
  let readingState = null;

  function delay(ms) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { waits.delete(cancel); resolve(); }, ms);
      const cancel = () => { clearTimeout(timer); waits.delete(cancel); reject(new Error('阅读等待已取消')); };
      waits.add(cancel);
    });
  }

  function resourceInfo() {
    const timed = /\/readsvr\/book\/mooc/.test(location.pathname) && Boolean(document.querySelector('#reader'));
    const book = /\/readsvr\/book\/mooc/.test(location.pathname) && Boolean(window.readweb?.goto);
    const ppt = Boolean(document.querySelector('.swiper-container .swiper-slide'));
    const read = Boolean(document.querySelector('#img.imglook'));
    const link = Boolean(document.querySelector('#hyperlink'));
    return { kind: timed ? 'timed_reading' : book ? 'book' : ppt ? 'audio_ppt' : read ? 'document' : link ? 'hyperlink' : document.querySelector('video,audio') ? 'media' : 'unknown',
      ready: timed ? Boolean(document.querySelector('#pagejump')) : book || (ppt && typeof window.swiperNext === 'function') || (read && typeof window.finishJob === 'function') || link,
      slide_count: document.querySelectorAll('.swiper-container .swiper-slide').length };
  }

  function getChapterInfos(root = document) {
    return Array.from(root.querySelectorAll('[onclick^="getTeacherAjax"],.chapter_item[onclick^="toOld("]')).map(el => {
      const modern = el.matches('.chapter_item[onclick^="toOld("]');
      const args = [...(el.getAttribute('onclick') || '').matchAll(/['"]([^'"]*)['"]/g)].map(match => match[1]);
      const parent = modern ? el : el.parentElement;
      const value = modern ? parent.querySelector('.catalog_task .catalog_points_yi')?.textContent?.trim() : parent?.querySelector('.jobUnfinishCount')?.value;
      const remaining = value != null && /^\d+$/.test(value) ? Number(value) : null;
      const completeMarker = Boolean(parent?.querySelector(modern ? '.catalog_task .catalog_state.icon_yiwanc' : '.icon_Completed'));
      const chapterId = args[modern ? 1 : 2] || '';
      if (chapterId) el.setAttribute("data-studio-learning-chapter", chapterId);
      return { chapterId, title: clean(el.getAttribute('title') || el.innerText || el.textContent), remaining,
        completed: remaining === 0 || (remaining === null && completeMarker), element: el };
    }).filter(item => item.chapterId);
  }

  function searchIFrame(root) {
    let list = Array.from(root.querySelectorAll('iframe'));
    const result = [];
    while (list.length) {
      const frame = list.shift();
      try {
        if (frame?.contentWindow?.document) {
          result.push(frame);
          list = list.concat(Array.from(frame.contentWindow.document.querySelectorAll('iframe')));
        }
      } catch { /* Cross-origin frames are inspected by the host through Playwright. */ }
    }
    return result;
  }

  function waitForMedia(options = {}) {
    const token = generation;
    return new Promise((resolve, reject) => {
      let interval, timeout;
      const finish = (media, error) => {
        clearInterval(interval); clearTimeout(timeout); waits.delete(cancel);
        if (error) reject(error); else resolve(media);
      };
      const cancel = () => finish(null, new Error('播放等待已取消'));
      waits.add(cancel);
      interval = setInterval(() => {
        if (generation !== token) return cancel();
        const media = document.querySelector(`${options.videoSelector || 'video'},${options.audioSelector || 'audio'}`);
        if (media) finish(media);
      }, 200);
      timeout = setTimeout(() => finish(null, new Error('视频或音频加载超时')), options.timeout || 10000);
    });
  }

  function attachments() {
    const raw = Array.isArray(window.attachments) ? window.attachments : [];
    return raw.map(item => {
      const property = item.property || {};
      return { id: String(item.jobid || property._jobid || property.mid || ''), mid: String(property.mid || ''),
        title: clean(property.name || property.title || property.bookname || '任务点'),
        mandatory: Boolean(item.job || item.isPassed === true), completed: item.isPassed === true,
        kind: String(item.type || property.module || property.type || ''), objectId: String(property.objectid || '') };
    }).filter(item => item.id);
  }

  function snapshot() {
    const media = document.querySelector('video,audio');
    const quiz = [...document.querySelectorAll('.ans-videoquiz,.TiMu')].some(visible);
    const challenge = [...document.querySelectorAll('#captcha,.maskDiv,.faceRecognition,.face-recognition')]
      .some(node => visible(node) && /人脸|验证|识别/.test(node.innerText || ''));
    const errorText = clean(document.querySelector('.vjs-modal-dialog-content')?.innerText);
    const error = /视频文件损坏|网络错误|无法加载|格式不支持/.test(errorText) ? errorText : '';
    const chapters = getChapterInfos().map(({ element, ...item }) => item);
    return { url: location.href, title: document.title, chapters, attachments: attachments(), quiz, challenge, error, resource: resourceInfo(), generation,
      read: Boolean(document.querySelector('#img.imglook,.swiper-container,#hyperlink,iframe[name="bookifame"]')),
      nestedFrames: searchIFrame(document).length,
      media: media ? { paused: media.paused, ended: media.ended, position: Number.isFinite(media.currentTime) ? media.currentTime : 0,
        duration: Number.isFinite(media.duration) ? media.duration : 0, ready: media.readyState, kind: media.tagName.toLowerCase() } : null };
  }

  async function play(options = {}) {
    const token = generation;
    const media = await waitForMedia({ timeout: (options.resource_wait_seconds || 15) * 1000 });
    if (!media.paused || media.ended) return { started: true, adopted: true, generation: token };
    media.volume = Math.max(0, Math.min(1, (options.volume || 0) / 100));
    media.muted = media.volume === 0;
    media.playbackRate = options.playback_rate || 1;
    try { await media.play(); }
    catch (error) { return { started: !media.paused || media.ended, generation: token, cancelled: token !== generation, error: error.name, reason: String(error.message || error) }; }
    return { started: !media.paused || media.ended, generation: token, cancelled: token !== generation, reason: media.paused && !media.ended ? '播放器未保持播放，请在实时浏览器点击播放后继续' : undefined };
  }

  function pause() {
    generation++;
    readingState = null;
    for (const cancel of [...waits]) cancel();
    const list = [...document.querySelectorAll('video,audio')];
    list.forEach(media => media.pause());
    return { paused: list.every(media => media.paused), mediaCount: list.length };
  }

  async function readResource() {
    const info = resourceInfo();
    if (snapshot().challenge || snapshot().quiz) return { blocked: true, kind: info.kind };
    const studyHandlers = window.__mumuStudyRuntime?.cx;
    if (!studyHandlers) throw new Error('本地阅读处理函数未加载');
    if (info.kind === 'document' && typeof window.finishJob === 'function') {
      await studyHandlers.read(window);
      return { handled: true, kind: info.kind, method: 'platform_finishJob' };
    }
    if (info.kind === 'audio_ppt' && typeof window.swiperNext === 'function') {
      await studyHandlers.readPPTWithAudio(window);
      return { handled: true, kind: info.kind, slides: info.slide_count };
    }
    if (info.kind === 'hyperlink') {
      await studyHandlers.hyperlink(document.querySelector('#hyperlink'));
      return { handled: true, kind: info.kind };
    }
    if (info.kind === 'book' || info.kind === 'timed_reading') {
      const currentPage = document.querySelector('#pagejump')?.value;
      readingState ||= { phase: currentPage === '7' ? 2 : currentPage === '5' ? 1 : 0, since: Date.now() };
      const rawTiming = new URL(location.href).searchParams.get('timing');
      const timing = rawTiming !== null && Number.isFinite(Number(rawTiming)) ? Math.max(0, Number(rawTiming)) : 60;
      const seconds = info.kind === 'book' ? 5 : timing + 3;
      if (Date.now() - readingState.since < seconds * 1000) return { waiting: true, kind: info.kind, phase: readingState.phase, wait_seconds: seconds };
      if (info.kind === 'book') {
        if (!Number.isFinite(Number(window.epage))) return { unsupported: true, reason: '阅读器没有提供末页信息' };
        window.readweb.goto(window.epage);
        return { handled: true, kind: info.kind };
      }
      const jumper = document.querySelector('#pagejump');
      if (!jumper) return { waiting: true, kind: info.kind };
      if (readingState.phase < 2) {
        const value = readingState.phase === 0 ? '5' : '7';
        if (![...jumper.options].some(option => option.value === value)) return { unsupported: true, reason: '计时阅读器页码结构发生变化' };
        jumper.value = value; jumper.dispatchEvent(new Event('change', { bubbles: true }));
        readingState = { phase: readingState.phase + 1, since: Date.now() };
        return { waiting: true, kind: info.kind, phase: readingState.phase, wait_seconds: seconds };
      }
      const pager = [...document.querySelectorAll('.readerPager')].find(el => el.style.zIndex === '101');
      if (!pager) return { unsupported: true, reason: '计时阅读器未出现可确认的封底入口' };
      pager.click();
      return { handled: true, kind: info.kind };
    }
    return { unsupported: true, kind: info.kind, reason: '阅读控件尚未就绪或当前阅读器没有受支持的完成入口' };
  }

  function resourceTabs() {
    const tabs = [...document.querySelectorAll('#prev_tab .prev_ul li')];
    const source = document.querySelector('iframe[src*="knowledge/cards"]')?.src;
    let current = tabs.findIndex(el => /(?:^|\s)(?:active|on|current)(?:\s|$)/.test(el.className));
    if (current < 0 && source) current = Number(new URL(source, location.href).searchParams.get('num'));
    tabs.forEach((el, index) => el.setAttribute('data-studio-resource-tab', String(index)));
    return { count: tabs.length, current, next: current >= 0 && current + 1 < tabs.length ? current + 1 : null };
  }

  function openChapter(id) {
    const chapter = getChapterInfos().find(item => item.chapterId === id);
    if (!chapter) throw new Error('章节目录已变化，请重新采集');
    chapter.element.click();
    return { opened: true, chapterId: id };
  }

  function parseCatalog(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return getChapterInfos(doc).map(({ element, ...item }) => item);
  }
  window.__studioLearning = { version: 'mumu-learning-runtime-v1', snapshot, play, pause, stop: pause, openChapter, parseCatalog, readResource, resourceTabs, delay };
})();
