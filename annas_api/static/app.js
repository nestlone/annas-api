/* Minimal console for the annas-api service: no build step, no dependencies. */

const API = {
  async request(path, options = {}) {
    const init = { credentials: "same-origin", ...options };
    if (init.body !== undefined && typeof init.body !== "string") {
      init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
      init.body = JSON.stringify(init.body);
    }
    const response = await fetch(path, init);
    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
    }
    if (!response.ok) {
      const error = new Error((data && data.detail) || "请求失败（" + response.status + "）");
      error.status = response.status;
      throw error;
    }
    return data;
  },
  get: (p) => API.request(p),
  post: (p, body) => API.request(p, { method: "POST", body: body || {} }),
  patch: (p, body) => API.request(p, { method: "PATCH", body: body || {} }),
  put: (p, body) => API.request(p, { method: "PUT", body: body || {} }),
  del: (p) => API.request(p, { method: "DELETE" }),
};

const state = { me: null, settings: null };

const NAV_ICONS = {
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.3"></circle><path d="m16 16 4.2 4.2"></path></svg>',
  jobs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3h8l4 4v14H4V3h4"></path><path d="M8 3v5h8V3M8 14h8M8 18h5"></path></svg>',
  keys: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8.5" cy="15.5" r="3.5"></circle><path d="m11 13 8.5-8.5M16 8l2 2M14 10l2 2"></path></svg>',
  usage: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19V9M10 19V5M16 19v-8M22 19V3"></path></svg>',
  admin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="3.2"></circle><path d="M5.5 21a6.5 6.5 0 0 1 13 0M19 8h3M20.5 6.5v3"></path></svg>',
};

const view = () => document.getElementById("view");
const esc = (value) =>
  String(value === null || value === undefined ? "" : value).replace(
    /[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

function toast(message, kind) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.className = "toast" + (kind ? " toast-" + kind : "");
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 4000);
}

function fmtTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}

const STATUS_LABEL = {
  queued: "排队中", running: "进行中", completed: "已完成",
  failed: "失败", cancelled: "已取消",
};

function statusBadge(status) {
  return '<span class="badge badge-' + esc(status) + '">' + esc(STATUS_LABEL[status] || status) + "</span>";
}

async function pollJob(id, onUpdate) {
  for (let attempt = 0; attempt < 300; attempt++) {
    const job = await API.get("/v1/jobs/" + id);
    if (onUpdate) onUpdate(job);
    if (["completed", "failed", "cancelled"].includes(job.status)) return job;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("任务等待超时");
}

/* ------------------------------------------------------------------ chrome */

function renderChrome() {
  const nav = document.getElementById("nav");
  const account = document.getElementById("account");
  if (!state.me) {
    nav.innerHTML = "";
    account.innerHTML = "";
    return;
  }
  const links = [
    ["#/search", "检索", "search"],
    ["#/jobs", "我的任务", "jobs"],
    ["#/keys", "API 密钥", "keys"],
    ["#/usage", "我的额度", "usage"],
  ];
  if (state.me.is_admin) links.push(["#/admin", "管理后台", "admin"]);
  const active = location.hash || "#/search";
  nav.innerHTML = links
    .map(([href, label, icon]) =>
      '<a href="' + href + '" class="' + (active === href ? "active" : "") + '">' + NAV_ICONS[icon] + '<span>' + esc(label) + "</span></a>")
    .join("");
  account.innerHTML =
    '<span class="who">' + esc(state.me.username) + (state.me.is_admin ? " · 管理员" : "") + "</span>" +
    '<button class="ghost" id="logout">退出</button>';
  document.getElementById("logout").onclick = async () => {
    await API.post("/web/logout");
    state.me = null;
    location.hash = "";
    boot();
  };
}

function field(label, input) {
  return '<label class="field"><span>' + esc(label) + "</span>" + input + "</label>";
}

/* ------------------------------------------------------------------ login */

async function screenLogin() {
  state.me = null;
  renderChrome();
  state.settings = await API.get("/web/settings");
  const canRegister = state.settings.registration_open || state.settings.setup_required;
  const minLength = state.settings.min_password_length || 8;
  view().innerHTML =
    '<div class="card narrow">' +
    "<h1>登录控制台</h1>" +
    (state.settings.setup_required
      ? '<p class="hint">尚未创建任何账号，第一个注册的账号将成为管理员。</p>'
      : "") +
    '<form id="login-form">' +
    field("用户名", '<input name="username" autocomplete="username" required>') +
    field("密码", '<input name="password" type="password" autocomplete="current-password" required>') +
    '<button type="submit">登录</button>' +
    "</form>" +
    (canRegister
      ? '<hr><form id="register-form">' +
        "<h2>注册</h2>" +
        field("用户名", '<input name="username" autocomplete="username" required>') +
        field("密码", '<input name="password" type="password" minlength="' + minLength + '" autocomplete="new-password" required>') +
        '<button type="submit" class="ghost">注册</button>' +
        "</form>"
      : '<p class="hint">注册未开放。</p>') +
    "</div>";

  document.getElementById("login-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      state.me = await API.post("/web/login", {
        username: form.username.value,
        password: form.password.value,
      });
      location.hash = "#/search";
      boot();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const registerForm = document.getElementById("register-form");
  if (registerForm) {
    registerForm.onsubmit = async (event) => {
      event.preventDefault();
      try {
        state.me = await API.post("/web/register", {
          username: event.target.username.value,
          password: event.target.password.value,
        });
        toast("注册成功", "ok");
        location.hash = "#/search";
        boot();
      } catch (error) {
        toast(error.message, "error");
      }
    };
  }
}

/* ------------------------------------------------------------------ search */

async function screenSearch() {
  view().innerHTML =
    '<div class="card">' +
    "<h1>在线检索</h1>" +
    '<form id="search-form" class="row">' +
    '<input name="query" placeholder="书名或关键词" required>' +
    '<input name="ext" placeholder="格式（可选）" class="small">' +
    '<input name="limit" type="number" min="1" max="50" value="10" class="small">' +
    '<button type="submit">检索</button>' +
    "</form>" +
    '<div id="search-status" class="hint"></div>' +
    '<div id="search-result"></div>' +
    "</div>";

  document.getElementById("search-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    const status = document.getElementById("search-status");
    const result = document.getElementById("search-result");
    result.innerHTML = "";
    status.textContent = "正在提交…";
    try {
      const created = await API.post("/v1/search", {
        query: form.query.value,
        ext: form.ext.value || null,
        limit: Number(form.limit.value) || 10,
      });
      status.textContent = "任务已提交，正在检索…";
      const job = await pollJob(created.id, (current) => {
        status.textContent = "状态：" + (STATUS_LABEL[current.status] || current.status);
      });
      if (job.status !== "completed") {
        status.textContent = "";
        throw new Error(job.error || "检索失败");
      }
      status.textContent = "共 " + (job.result || []).length + " 条结果";
      renderSearchResults(job.result || []);
    } catch (error) {
      status.textContent = "";
      toast(error.message, "error");
    }
  };
}

function renderSearchResults(hits) {
  const node = document.getElementById("search-result");
  if (!hits.length) {
    node.innerHTML = '<p class="hint">没有匹配的结果。</p>';
    return;
  }
  node.innerHTML =
    '<table><thead><tr><th>标题</th><th>格式</th><th>大小</th><th></th></tr></thead><tbody>' +
    hits.map((hit, index) =>
      "<tr><td>" + esc(hit.title || hit.md5) + "</td><td>" + esc(hit.format || "-") +
      "</td><td>" + esc(hit.size || "-") + '</td><td class="right"><button data-index="' + index +
      '" class="ghost">下载</button></td></tr>').join("") +
    "</tbody></table>";
  node.querySelectorAll("button[data-index]").forEach((button) => {
    button.onclick = () => startDownload(hits[Number(button.dataset.index)], button);
  });
}

async function startDownload(hit, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "下载中…";
  try {
    const created = await API.post("/v1/downloads", { md5: hit.md5, name: hit.title });
    const job = await pollJob(created.id, (current) => {
      button.textContent = STATUS_LABEL[current.status] || current.status;
    });
    if (job.status !== "completed") throw new Error(job.error || "下载失败");
    button.textContent = "已就绪";
    const link = document.createElement("a");
    link.href = job.download_url;
    link.textContent = " 保存文件";
    link.className = "download-link";
    button.after(link);
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ jobs */

async function screenJobs() {
  view().innerHTML =
    '<div class="card">' +
    '<div class="row spread"><h1>我的任务</h1><button id="refresh" class="ghost">刷新</button></div>' +
    '<div id="jobs-body" class="hint">加载中…</div>' +
    "</div>";
  document.getElementById("refresh").onclick = screenJobs;
  await loadJobs();
}

async function loadJobs() {
  const body = document.getElementById("jobs-body");
  try {
    const data = await API.get("/v1/jobs?limit=50");
    if (!data.jobs.length) {
      body.innerHTML = '<p class="hint">还没有任务。</p>';
      return;
    }
    body.innerHTML =
      '<table><thead><tr><th>类型</th><th>状态</th><th>创建时间</th><th></th></tr></thead><tbody>' +
      data.jobs.map((job) =>
        "<tr><td>" + (job.kind === "search" ? "检索" : "下载") + "</td><td>" +
        statusBadge(job.status) + "</td><td>" + esc(fmtTime(job.created_at)) +
        '</td><td class="right">' +
        (job.status === "queued" ? '<button class="ghost" data-cancel="' + esc(job.id) + '">取消</button>' : "") +
        (job.download_url ? '<a class="download-link" href="' + esc(job.download_url) + '">下载文件</a>' : "") +
        "</td></tr>").join("") +
      "</tbody></table>";
    body.querySelectorAll("button[data-cancel]").forEach((button) => {
      button.onclick = async () => {
        try {
          await API.post("/v1/jobs/" + button.dataset.cancel + "/cancel");
          toast("已取消", "ok");
          loadJobs();
        } catch (error) {
          toast(error.message, "error");
        }
      };
    });
  } catch (error) {
    body.innerHTML = '<p class="hint">' + esc(error.message) + "</p>";
  }
}

/* ------------------------------------------------------------------ keys */

async function screenKeys() {
  view().innerHTML =
    '<div class="card">' +
    "<h1>API 密钥</h1>" +
    '<p class="hint">密钥只在创建时显示一次，请立即保存。调用接口时放在 <code>X-API-Key</code> 请求头。</p>' +
    '<form id="key-form" class="row">' +
    '<input name="name" placeholder="备注（可选）">' +
    '<button type="submit">新建密钥</button>' +
    "</form>" +
    '<div id="new-key"></div>' +
    '<div id="keys-body" class="hint">加载中…</div>' +
    "</div>";
  document.getElementById("key-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const created = await API.post("/web/keys", { name: event.target.name.value });
      document.getElementById("new-key").innerHTML =
        '<div class="callout"><strong>新密钥（仅此一次）</strong><code>' + esc(created.key) + "</code></div>";
      event.target.reset();
      loadKeys();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  await loadKeys();
}

async function loadKeys() {
  const body = document.getElementById("keys-body");
  const data = await API.get("/web/keys");
  if (!data.keys.length) {
    body.innerHTML = '<p class="hint">还没有密钥。</p>';
    return;
  }
  const prefix = "X-API-Key: 示例";
  body.innerHTML =
    '<table><thead><tr><th>备注</th><th>前缀</th><th>创建时间</th><th>最近使用</th><th>状态</th><th></th></tr></thead><tbody>' +
    data.keys.map((key) =>
      "<tr><td>" + esc(key.name || "-") + "</td><td><code>" + esc(key.key_prefix) + "…</code></td><td>" +
      esc(fmtTime(key.created_at)) + "</td><td>" + esc(fmtTime(key.last_used_at)) + "</td><td>" +
      (key.is_active ? "启用" : "已吊销") + '</td><td class="right">' +
      (key.is_active ? '<button class="ghost" data-revoke="' + key.id + '">吊销</button>' : "") +
      "</td></tr>").join("") +
    "</tbody></table>";
  body.querySelectorAll("button[data-revoke]").forEach((button) => {
    button.onclick = async () => {
      try {
        await API.post("/web/keys/" + button.dataset.revoke + "/revoke");
        toast("已吊销", "ok");
        loadKeys();
      } catch (error) {
        toast(error.message, "error");
      }
    };
  });
}

/* ------------------------------------------------------------------ usage */

function quotaBar(label, used, limit) {
  const text = limit ? used + " / " + limit : used + " / 不限";
  const percent = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    '<div class="quota"><div class="quota-head"><span>' + esc(label) + "</span><span>" + esc(text) +
    '</span></div><div class="bar"><i style="width:' + percent + '%"></i></div></div>'
  );
}

async function screenUsage() {
  view().innerHTML = '<div class="card"><h1>我的额度</h1><div id="usage-body" class="hint">加载中…</div></div>';
  try {
    const data = await API.get("/web/usage");
    const quota = data.quota || {};
    const usage = data.usage || {};
    document.getElementById("usage-body").innerHTML =
      quotaBar("今日检索次数", usage.searches || 0, quota.daily_searches || 0) +
      quotaBar("今日下载次数", usage.downloads || 0, quota.daily_downloads || 0) +
      quotaBar("正在进行的任务", usage.active_jobs || 0, quota.max_concurrent_jobs || 0) +
      '<p class="hint">统计日期（UTC）：' + esc(usage.day) + "。额度为 0 表示不限制。</p>";
  } catch (error) {
    document.getElementById("usage-body").innerHTML = '<p class="hint">' + esc(error.message) + "</p>";
  }
}

/* ------------------------------------------------------------------ admin */

async function screenAdmin() {
  if (!state.me || !state.me.is_admin) { location.hash = "#/search"; return; }
  view().innerHTML =
    '<div class="card">' +
    "<h1>管理后台</h1>" +
    '<div class="row spread"><label class="switch"><input type="checkbox" id="reg"> 开放注册</label>' +
    '<span class="hint" id="user-count"></span></div>' +
    '<hr><form id="new-user" class="row">' +
    '<input name="username" placeholder="用户名" required>' +
    '<input name="password" type="password" placeholder="密码" required>' +
    '<label class="switch"><input type="checkbox" name="is_admin"> 管理员</label>' +
    '<button type="submit">新建用户</button>' +
    "</form>" +
    '<div id="users-body" class="hint">加载中…</div>' +
    "</div>";

  const settings = await API.get("/web/admin/settings");
  document.getElementById("reg").checked = !!settings.registration_open;
  document.getElementById("user-count").textContent = "共 " + settings.user_count + " 个用户";
  document.getElementById("reg").onchange = async (event) => {
    try {
      await API.patch("/web/admin/settings", { registration_open: event.target.checked });
      toast("已更新", "ok");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  document.getElementById("new-user").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await API.post("/web/admin/users", {
        username: form.username.value,
        password: form.password.value,
        is_admin: form.is_admin.checked,
      });
      form.reset();
      toast("已创建", "ok");
      loadUsers();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  await loadUsers();
}

async function loadUsers() {
  const body = document.getElementById("users-body");
  const data = await API.get("/web/admin/users?limit=200");
  body.innerHTML =
    '<table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>今日检索</th><th>今日下载</th>' +
    "<th>检索额度</th><th>下载额度</th><th>并发</th><th></th></tr></thead><tbody>" +
    data.users.map((user) => {
      const quota = user.quota || {};
      const usage = user.usage || {};
      return (
        '<tr data-user="' + user.id + '">' +
        "<td>" + esc(user.username) + "</td>" +
        "<td>" + (user.is_admin ? "管理员" : "用户") + "</td>" +
        "<td>" + (user.is_active ? "启用" : "停用") + "</td>" +
        "<td>" + (usage.searches || 0) + "</td>" +
        "<td>" + (usage.downloads || 0) + "</td>" +
        '<td><input class="tiny" data-quota="daily_searches" type="number" min="0" value="' + (quota.daily_searches || 0) + '"></td>' +
        '<td><input class="tiny" data-quota="daily_downloads" type="number" min="0" value="' + (quota.daily_downloads || 0) + '"></td>' +
        '<td><input class="tiny" data-quota="max_concurrent_jobs" type="number" min="0" value="' + (quota.max_concurrent_jobs || 0) + '"></td>' +
        '<td class="right nowrap">' +
        '<button class="ghost" data-save>保存额度</button> ' +
        '<button class="ghost" data-toggle>' + (user.is_active ? "停用" : "启用") + "</button> " +
        '<button class="ghost" data-reset>重置用量</button> ' +
        '<button class="ghost danger" data-delete>删除</button>' +
        "</td></tr>"
      );
    }).join("") +
    "</tbody></table>" +
    '<p class="hint">额度为 0 表示不限制；“重置用量”清零该用户今日的检索/下载计数。</p>';

  body.querySelectorAll("tr[data-user]").forEach((row) => {
    const userId = row.dataset.user;
    const readQuota = () => {
      const values = {};
      row.querySelectorAll("input[data-quota]").forEach((input) => {
        values[input.dataset.quota] = Number(input.value) || 0;
      });
      return values;
    };
    row.querySelector("[data-save]").onclick = async () => {
      try {
        await API.put("/web/admin/users/" + userId + "/quota", readQuota());
        toast("额度已保存", "ok");
      } catch (error) { toast(error.message, "error"); }
    };
    row.querySelector("[data-toggle]").onclick = async () => {
      const active = row.children[2].textContent === "启用";
      try {
        await API.patch("/web/admin/users/" + userId, { is_active: !active });
        loadUsers();
      } catch (error) { toast(error.message, "error"); }
    };
    row.querySelector("[data-reset]").onclick = async () => {
      try {
        await API.post("/web/admin/users/" + userId + "/usage/reset");
        toast("用量已重置", "ok");
        loadUsers();
      } catch (error) { toast(error.message, "error"); }
    };
    row.querySelector("[data-delete]").onclick = async () => {
      if (!confirm("确定删除该用户？其密钥与额度会一并删除，历史任务保留。")) return;
      try {
        await API.del("/web/admin/users/" + userId);
        toast("已删除", "ok");
        loadUsers();
      } catch (error) { toast(error.message, "error"); }
    };
  });
}

/* ------------------------------------------------------------------ router */

const ROUTES = {
  "#/search": screenSearch,
  "#/jobs": screenJobs,
  "#/keys": screenKeys,
  "#/usage": screenUsage,
  "#/admin": screenAdmin,
};

async function route() {
  if (!state.me) return screenLogin();
  const screen = ROUTES[location.hash] || screenSearch;
  renderChrome();
  try {
    await screen();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function boot() {
  try {
    state.me = await API.get("/web/me");
  } catch (error) {
    state.me = null;
  }
  renderChrome();
  if (!state.me) return screenLogin();
  await route();
}

window.addEventListener("hashchange", () => { if (state.me) route(); });
boot();
