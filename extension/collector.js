const WORK_LIST_BASE = "https://mooc1.chaoxing.com/mooc2/work/list";

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

async function setSyncState(state) {
  await chrome.runtime.sendMessage({
    type: "UPDATE_ASSIGNMENT_SYNC_STATE",
    state: {
      ...state,
      updatedAt: new Date().toISOString()
    }
  });
  const message = document.querySelector("#message");
  if (message) {
    message.textContent = state.message || "正在同步作业...";
  }
}

async function fetchText(url, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      credentials: "include",
      redirect: "follow",
      cache: "no-store",
      signal: controller.signal
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`请求失败 ${response.status}`);
    }
    if (/passport|login/i.test(new URL(response.url).pathname)) {
      throw new Error("Chaoxing 登录已失效");
    }
    return {
      url: response.url,
      status: response.status,
      text
    };
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("请求超时");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function parseHtml(text) {
  return new DOMParser().parseFromString(text, "text/html");
}

function findCourseFrameUrl(documentNode, baseUrl) {
  const frame = documentNode.querySelector("iframe#frame_content")
    || documentNode.querySelector("iframe[src*='/visit/interaction']");
  const source = frame?.getAttribute("src");
  return source ? new URL(source, baseUrl).href : "";
}

function parseCourses(documentNode, baseUrl) {
  const seen = new Set();
  return [...documentNode.querySelectorAll("#courseList .course-name")]
    .map((nameElement) => {
      const card = nameElement.closest(".course") || nameElement.closest("[id^='c_']");
      const cardText = normalizeText(card?.innerText || card?.textContent || "");
      const startDate = (cardText.match(/开课时间：(\d{4}-\d{2}-\d{2})/) || [])[1] || "";
      const link = card?.querySelector("a[href*='stucoursemiddle']")
        || nameElement.closest("a");
      const source = link?.getAttribute("href");
      const url = source ? new URL(source, baseUrl).href : "";
      const parsed = url ? new URL(url) : null;
      return {
        courseId: parsed?.searchParams.get("courseid") || "",
        classId: parsed?.searchParams.get("clazzid") || "",
        cpi: parsed?.searchParams.get("cpi") || "",
        courseName: nameElement.getAttribute("title")
          || normalizeText(nameElement.textContent),
        startDate,
        ended: /课程已结束/.test(cardText),
        url
      };
    })
    .filter((course) => {
      if (!course.url || !course.courseName) {
        return false;
      }
      const key = `${course.courseId}_${course.classId}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function hiddenValue(documentNode, id) {
  return documentNode.querySelector(`#${id}`)?.value || "";
}

function buildWorkListUrl(courseDocument, courseResponseUrl, course) {
  const responseUrl = new URL(courseResponseUrl);
  const courseId = hiddenValue(courseDocument, "courseid")
    || responseUrl.searchParams.get("courseid")
    || course.courseId;
  const classId = hiddenValue(courseDocument, "clazzid")
    || responseUrl.searchParams.get("clazzid")
    || course.classId;
  const cpi = hiddenValue(courseDocument, "cpi")
    || responseUrl.searchParams.get("cpi")
    || course.cpi;
  const t = hiddenValue(courseDocument, "t")
    || responseUrl.searchParams.get("t");
  const stuenc = hiddenValue(courseDocument, "enc")
    || responseUrl.searchParams.get("enc");
  const workEnc = hiddenValue(courseDocument, "workEnc");
  if (!courseId || !classId || !cpi || !t || !stuenc || !workEnc) {
    throw new Error("课程页缺少作业列表参数");
  }

  const url = new URL(WORK_LIST_BASE);
  url.search = new URLSearchParams({
    courseId,
    classId,
    cpi,
    ut: "s",
    t,
    stuenc,
    enc: workEnc
  }).toString();
  return url.href;
}

function parseAssignments(documentNode) {
  return [...documentNode.querySelectorAll("li")]
    .filter((item) => {
      const status = item.querySelector(".status")?.textContent?.trim();
      return ["未交", "未完成", "已完成", "待互评"].includes(status);
    })
    .map((item) => {
      const taskUrl = item.getAttribute("data") || "";
      let workId = "";
      try {
        workId = new URL(taskUrl, location.href).searchParams.get("workId") || "";
      } catch {
        workId = "";
      }
      return {
        title: normalizeText(
          item.querySelector("p.overHidden2")?.textContent
          || item.getAttribute("aria-label")?.split(";")[0]
        ),
        status: item.querySelector(".status")?.textContent?.trim() || "",
        timeText: normalizeText(item.querySelector(".time")?.textContent),
        taskUrl,
        workId
      };
    })
    .filter((item) => item.title);
}

function inspectSubmissionDocument(documentNode, finalUrl) {
  const visible = (element) => {
    const style = documentNode.defaultView?.getComputedStyle(element);
    return !element.hidden
      && style?.display !== "none"
      && style?.visibility !== "hidden"
      && !element.disabled;
  };
  const controls = [
    ...documentNode.querySelectorAll(
      "button, a, input[type='button'], input[type='submit'], [role='button']"
    )
  ].filter(visible);
  const controlText = (element) => normalizeText(
    element.textContent || element.value || element.getAttribute("aria-label")
  );
  const hasSubmit = controls.some((element) => (
    /提交/.test(controlText(element)) && !element.disabled
  ));
  const hasStart = controls.some((element) => (
    /开始答题|继续答题|作答/.test(controlText(element)) && !element.disabled
  ));
  const text = normalizeText(documentNode.body?.textContent);
  const deadlineMatch = text.match(
    /作答时间[:：]?\s*(\d{2}-\d{2}\s+\d{2}:\d{2}\s*至\s*\d{2}-\d{2}\s+\d{2}:\d{2})/
  );
  return {
    finalTaskUrl: finalUrl,
    deadlineText: deadlineMatch?.[1] || "",
    submittable: !finalUrl.includes("/work/preview") && (hasSubmit || hasStart)
  };
}

async function runAssignmentSync() {
  const config = await chrome.runtime.sendMessage({
    type: "GET_ASSIGNMENT_SYNC_CONFIG"
  });
  if (!config?.ok) {
    throw new Error(config?.error || "无法读取同步配置");
  }
  const request = config.request || { mode: "manual", academicYear: 2026 };
  const academicYear = Number(request.academicYear) || 2026;
  const serverUrl = (
    config.serverUrl || "http://127.0.0.1:8787"
  ).replace(/\/+$/, "");
  const allAssignments = [];
  const errors = [];

  await setSyncState({
    status: "running",
    mode: request.mode,
    academicYear,
    message: "正在无头读取课程列表..."
  });

  const baseResponse = await fetchText("https://i.chaoxing.com/base?ws=1");
  const baseDocument = parseHtml(baseResponse.text);
  const courseListUrl = findCourseFrameUrl(baseDocument, baseResponse.url);
  if (!courseListUrl) {
    throw new Error("没有找到课程列表入口");
  }

  const courseListResponse = await fetchText(courseListUrl);
  const courseListDocument = parseHtml(courseListResponse.text);
  const courses = parseCourses(courseListDocument, courseListResponse.url)
    .filter((course) => (
      course.startDate.startsWith(`${academicYear}-`) && !course.ended
    ));
  if (courses.length === 0) {
    throw new Error(`没有找到 ${academicYear} 年开课的课程`);
  }

  for (let index = 0; index < courses.length; index += 1) {
    const course = courses[index];
    await setSyncState({
      status: "running",
      mode: request.mode,
      academicYear,
      message: `正在无头读取 ${index + 1}/${courses.length}：${course.courseName}`,
      processedCourses: index,
      totalCourses: courses.length
    });
    try {
      const courseResponse = await fetchText(course.url);
      const courseDocument = parseHtml(courseResponse.text);
      const workListUrl = buildWorkListUrl(courseDocument, courseResponse.url, course);
      const workListResponse = await fetchText(workListUrl);
      const workListDocument = parseHtml(workListResponse.text);
      for (const assignment of parseAssignments(workListDocument)) {
        allAssignments.push({
          ...assignment,
          academicYear,
          courseName: course.courseName,
          courseId: course.courseId,
          classId: course.classId,
          startDate: course.startDate
        });
      }
    } catch (error) {
      errors.push({
        courseName: course.courseName,
        courseId: course.courseId,
        classId: course.classId,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }

  const unfinishedAssignments = allAssignments.filter((assignment) => (
    assignment.status === "未交" || assignment.status === "未完成"
  ));
  for (let index = 0; index < unfinishedAssignments.length; index += 1) {
    const assignment = unfinishedAssignments[index];
    await setSyncState({
      status: "running",
      mode: request.mode,
      academicYear,
      message: `正在无头检查 ${index + 1}/${unfinishedAssignments.length}：${assignment.title}`,
      processedCourses: courses.length,
      totalCourses: courses.length
    });
    try {
      const taskResponse = await fetchText(assignment.taskUrl);
      const taskDocument = parseHtml(taskResponse.text);
      const availability = inspectSubmissionDocument(taskDocument, taskResponse.url);
      assignment.submittable = availability.submittable;
      assignment.deadlineText = availability.deadlineText;
      assignment.finalTaskUrl = availability.finalTaskUrl;
    } catch (error) {
      assignment.submittable = false;
      assignment.deadlineText = "";
      assignment.finalTaskUrl = "";
      errors.push({
        courseName: assignment.courseName,
        courseId: assignment.courseId,
        classId: assignment.classId,
        workId: assignment.workId,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  }

  await setSyncState({
    status: "running",
    mode: request.mode,
    academicYear,
    message: "正在写入本地作业接口...",
    processedCourses: courses.length,
    totalCourses: courses.length
  });

  const response = await fetch(`${serverUrl}/api/assignments/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: "chaoxing",
      academicYear,
      syncedAt: new Date().toISOString(),
      coursesScanned: courses.length,
      assignments: allAssignments,
      errors
    })
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "作业同步接口写入失败");
  }

  const submittableCount = allAssignments.filter((assignment) => (
    assignment.submittable
    && (assignment.status === "未交" || assignment.status === "未完成")
  )).length;
  await setSyncState({
    status: "success",
    mode: request.mode,
    academicYear,
    message: `无头同步完成：${submittableCount} 个当前可提交作业`,
    processedCourses: courses.length,
    totalCourses: courses.length,
    unfinishedCount: payload.unfinishedCount || 0,
    submittableCount,
    syncedAt: payload.syncedAt
  });
  return { submittableCount };
}

runAssignmentSync()
  .then((result) => {
    chrome.runtime.sendMessage({
      type: "ASSIGNMENT_SYNC_FINISHED",
      status: "success",
      submittableCount: result.submittableCount
    });
  })
  .catch(async (error) => {
    const message = error instanceof Error ? error.message : String(error);
    await setSyncState({
      status: "error",
      message
    });
    chrome.runtime.sendMessage({
      type: "ASSIGNMENT_SYNC_FINISHED",
      status: "error",
      error: message
    });
  });
