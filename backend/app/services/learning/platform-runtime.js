/* Mumu native course navigation and resource handlers. */
(() => {
  if (window.__studioCourse?.version === 'multi-platform.1') return;
  const studyHandlers = window.__mumuStudyRuntime;
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const hash = value => { let n=2166136261; for(const c of value) n=Math.imul(n^c.charCodeAt(0),16777619); return (n>>>0).toString(16); };
  const complete = item => item?.completed === true || item?.isFinish === true || item?.isFinished === true || Number(item?.progress ?? item?.studyProgress ?? item?.completionRate) === 100;
  const flatten = list => (list || []).flatMap(item => item?.children?.length ? flatten(item.children) : [item]);
  let nodes = new Map();

  function catalog(platform) {
    nodes = new Map();
    let entries = [], processor;
    const select = (selector, title, finished, kind = 'resource') => [...document.querySelectorAll(selector)].map(element => ({
      element, title: clean(title(element)), completed: Boolean(finished(element)), kind,
      id: element.dataset.id || element.dataset.leafId || element.getAttribute('itemid') || element.getAttribute('data-id') || element.id || '',
    }));
    if (platform === 'zhihuishu') {
      const handlers = studyHandlers.zhs;
      if (document.querySelector('.el-tree-node')) {
        processor = new handlers.HikeV2();
        entries = select('.el-tree-node', e => e.querySelector('.file-name')?.textContent || e.textContent, e => e.querySelector('label.success')).filter(item => !item.element.querySelector('.el-tree-node__children')?.children.length);
      } else if (document.querySelector('.source-icon')) {
        processor = new handlers.Hike();
        entries = [...document.querySelectorAll('.source-icon')].map(e => e.parentElement?.parentElement).filter(Boolean).map(element => ({element,title:clean(processor.getChapterName(element)),completed:!!element.querySelector('i.select')}));
      } else if (document.querySelector('.chapter-content .chapter-item')) {
        processor = new handlers.WishdomH5();
        const jobs = [...document.querySelectorAll('.chapter-content .chapter-item')].flatMap(e => e.querySelectorAll('.chapter-content-second').length ? [...e.querySelectorAll('.chapter-content-second')] : [e]);
        entries = jobs.map(element => ({element,title:clean(processor.getChapterName(element)),completed:!!element.querySelector('.finish-icon')}));
      } else if (document.querySelector('.child-main')) {
        processor = new handlers.StudyPlusH5();
        entries = select('.child-main', e => processor.getChapterName(e), e => e.parentElement?.querySelector('.finish-icon')).filter(item => item.element.parentElement?.querySelector('.child-time'));
      } else if (document.querySelector('.resources-item,.progress-num,.resource-box')) {
        processor = new handlers.FusionCourseH5();
        const cards = document.querySelectorAll('.resources-item');
        entries = cards.length ? select('.resources-item',e=>processor.getChapterName(e),e=>e.querySelector('.isFinish')) : select('.clearfix.video',e=>processor.getChapterName(e),e=> {
          const counts = clean(e.querySelector('.resource-text')?.textContent).replace('必学','').split('/').map(Number);
          return clean(e.querySelector('.progress-num')?.textContent)==='100%' || (counts.length===2 && counts[1]>0 && counts[0]===counts[1]);
        });
      } else {
        processor = new handlers.StudyVideoH5();
        entries = select('.clearfix.video',e=>processor.getChapterName(e),e=>e.querySelector('.time_icofinish'));
      }
      // Select the current platform's next actionable unit.
      if (entries.length) processor.getNext({next:false,restudy:false});
    } else if (platform === 'icourse') {
      entries = select('.learn-unit-tabs-scroll button',e=>e.textContent,e=>studyHandlers.icourse.finished(e));
      if (!entries.length) entries = select('.unitslist .unit',e=>e.querySelector('.unit-name')?.textContent||e.textContent,e=>e.querySelector('.lucide-circle-check'));
      if (!entries.length) entries = select('.unitslist li',e=>e.querySelector('.unit-name')?.textContent||e.textContent,e=>e.querySelector('.lucide-circle-check'));
    } else if (platform === 'icve') {
      entries = select('.s_point[itemtype]',e=>e.title||e.querySelector('.s_pointti')?.title||e.textContent,e=>e.querySelector('.done_icon_show'));
      if (!entries.length) {
        const links = studyHandlers.icve.directoryUrls();
        entries = [...document.querySelectorAll('.h_cells a')].filter(e=>links.includes(e.href)).map(element=>({element,title:clean(element.textContent),completed:false,url:element.href}));
      }
    } else if (platform === 'zjy') {
      const raw = document.querySelector('.coursePreviewIndex')?.__vue__?.list || document.querySelector('.classroom_activities')?.__vue__?.activeList || [];
      const original = document.querySelector('.classroom_activities') ? studyHandlers.zjy.getCourseDataInClassroomNowPage() : studyHandlers.zjy.zjyDataList();
      const leaves = flatten(raw);
      const elements = [...document.querySelectorAll('.items,.fIteml,.iChild')];
      entries = original.filter(item=>!['父节点','子节点'].includes(item.fileType)).map(item => {
        const originalItem = leaves.find(raw=>String(raw.id||raw.activityId)===String(item.id));
        const title = clean((item.levelName||'')+item.name);
        const element = elements.find(e=>clean(e.textContent).replace(/\s/g,'')===title.replace(/\s/g,''));
        return {element,id:String(item.id),title,completed:complete(originalItem),kind:item.fileType,courseDesignId:item.courseDesignId};
      });
    } else if (platform === 'yuketang') {
      entries = studyHandlers.yuketang.getJobs().map(element=>({element,id:element.dataset.id||element.dataset.leafId||'',title:clean(element.querySelector('.leaf-item-title')?.textContent||element.textContent),completed:!!element.querySelector('[class*=yuanquangou]'),kind:studyHandlers.yuketang.getJobTag(element)}));
      if (entries.length) studyHandlers.yuketang.next.call({cfg:{restudy:false}});
    }
    return entries.filter(item=>item.title).map((item,index)=> {
      const chapterId = item.id || platform+':'+hash(item.title+'|'+(item.url||'')+'|'+index);
      if(item.element) { item.element.setAttribute('data-studio-chapter',chapterId); nodes.set(chapterId,item.element); }
      return {chapterId,title:item.title,completed:item.completed,remaining:item.completed?0:null,kind:item.kind||'resource',url:item.url||'',courseDesignId:item.courseDesignId||'',navigable:!!item.element};
    });
  }

  function prepare(platform) {
    if(platform==='yuketang') document.querySelectorAll('.nav-item-title:not(.is-expand)').forEach(e=>e.click());
    if(platform==='icve') document.querySelectorAll('.s_sectionlist,.s_sectionwrap').forEach(e=>e.style.display='block');
    if(platform==='zjy') {
      const folder=[...document.querySelectorAll('.items')].find(e=>e.querySelector('.el-icon-arrow-right')&&!e.dataset.studioExpanded);
      if(folder) {folder.dataset.studioExpanded='true';folder.click();return {expanded:true};}
    }
    return {expanded:false};
  }

  const coursePaths = {
    zhihuishu: /videoStudy|studyH5|fusioncourse|hike|smart-course|course\/study/i,
    icourse: /\/learn\/|\/mooc\/learn|\/spoc\/learn/i,
    icve: /learnspace\/learn|dir_course\.html|\/study\/|\/course\/learn/i,
    zjy: /coursePreview|\/study\/course|\/course\/study/i,
    yuketang: /studentLog|student-courses|lms-graph|studentCards/i,
  };
  function discover(platform) {
    const courses = [], seen = new Set();
    for(const element of document.querySelectorAll('a[href]')) {
      const url=new URL(element.href,location.href);
      if(url.protocol!=='https:'||!coursePaths[platform]?.test(url.pathname)) continue;
      const title=clean(element.getAttribute('title')||element.querySelector('.course-name,.course-title,h3,h4')?.textContent||element.textContent);
      if(!title||seen.has(url.href)) continue;
      seen.add(url.href);
      const parameters=url.searchParams;
      courses.push({courseId:parameters.get('courseId')||parameters.get('courseid')||parameters.get('id')||hash(url.pathname+url.search+url.hash),classId:parameters.get('classId')||parameters.get('classroomId')||'',courseName:title,url:url.href,semester:null});
    }
    if(!courses.length && catalog(platform).length) courses.push({courseId:hash(location.href),classId:'',courseName:clean(document.querySelector('.course-name,.source-name,.header-title-wrap')?.textContent||document.title),url:location.href,semester:null});
    return courses;
  }

  function snapshot(platform) {
    const result=window.__studioLearning.snapshot();
    const media=document.querySelector('video,audio');
    return {...result,chapters:catalog(platform),attachments:[],media:result.media?{...result.media,source:media.currentSrc||media.src||'media'}:null,
      challenge:result.challenge || [...document.querySelectorAll('[class*=captcha],[id*=captcha],.geetest_panel,.yidun_popup')].some(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'),
      quiz:result.quiz || [...document.querySelectorAll('.lms-graph-exercise,.popup-test,.question-dialog')].some(e=>e.getClientRects().length)};
  }
  async function advance(platform, token) {
    if(platform!=='icourse') return {advanced:false};
    const click=async element=>{
      if(!element) throw new Error('课程导航入口未找到');
      const id='nav-'+hash(element.outerHTML);
      element.setAttribute('data-studio-navigation',id);
      await window.__studioCourseClick({token,id});
    };
    const next=document.querySelector('.learn-unit-tabs-scroll') ? await studyHandlers.icourse.nextV2({click}) : await studyHandlers.icourse.next();
    for(const item of next) await click(typeof item==='function'?item():item);
    return {advanced:next.length>0};
  }
  window.__studioCourse={version:'multi-platform.1',catalog,prepare,discover,snapshot,advance};
})();
