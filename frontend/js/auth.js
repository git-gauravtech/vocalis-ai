const API = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") 
  ? "http://localhost:8000" 
  : "";

// ── TOKEN MANAGEMENT ──────────────────────────────────
function setToken(token) { localStorage.setItem("mm_token", token); }
function getToken() { return localStorage.getItem("mm_token"); }
function setUser(user) { localStorage.setItem("mm_user", JSON.stringify(user)); }
function getUser() {
  try { return JSON.parse(localStorage.getItem("mm_user")); } catch { return null; }
}
function logout() {
  localStorage.removeItem("mm_token");
  localStorage.removeItem("mm_user");
  window.location.href = "/login.html";
}

// ── API HELPER ────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body instanceof FormData) delete headers["Content-Type"];

  const res = await fetch(API + path, { ...options, headers });
  if (res.status === 401) { logout(); return; }
  return res;
}

// ── GOOGLE LOGIN ──────────────────────────────────────
function handleGoogleCredential(response) {
  const id_token = response.credential;
  fetch(API + "/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token })
  })
  .then(r => r.json())
  .then(data => {
    if (data.token) {
      setToken(data.token);
      setUser(data.user);
      if (data.user.role === "admin") {
        window.location.href = "/admin/dashboard.html";
      } else {
        window.location.href = "/student/dashboard.html";
      }
    } else {
      showToast("Login failed. Please try again.", "error");
    }
  })
  .catch(() => showToast("Login failed. Please try again.", "error"));
}

// ── GUARD ─────────────────────────────────────────────
function requireAuth(role = null) {
  const token = getToken();
  const user = getUser();
  if (!token || !user) {
    window.location.href = "/login.html";
    return null;
  }
  if (role && user.role !== role) {
    window.location.href = user.role === "admin" ? "/admin/dashboard.html" : "/student/dashboard.html";
    return null;
  }
  return user;
}

function populateNav(user) {
  const nameEl = document.getElementById("nav-name");
  const avatarEl = document.getElementById("nav-avatar");
  if (nameEl) nameEl.textContent = user.name;
  if (avatarEl) {
    const initials = user.name ? user.name.split(' ').map(n => n[0]).join('').toUpperCase() : "U";
    // Force fallback if picture is empty string, null, or undefined
    const pic = (user.picture && user.picture.trim().length > 0) ? user.picture : null;
    const fallback = `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name)}&background=6366f1&color=fff&size=128`;
    
    avatarEl.src = pic || fallback;
    avatarEl.onerror = () => {
      console.warn("Avatar failed to load, using SVG fallback");
      avatarEl.src = "data:image/svg+xml;base64," + btoa(`
        <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
          <rect width="36" height="36" fill="#6366f1"/>
          <text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" fill="#fff" font-family="Arial" font-size="14" font-weight="bold">${initials}</text>
        </svg>
      `);
    };
  }
}

// ── TOAST ─────────────────────────────────────────────
function showToast(msg, type = "info") {
  const t = document.createElement("div");
  t.className = "toast";
  const colors = { error: "#ef4444", success: "#22c55e", info: "#6c63ff" };
  t.style.borderLeft = `3px solid ${colors[type] || colors.info}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ── FORMAT DATE ───────────────────────────────────────
function formatDate(dateStr) {
  if (!dateStr) return "—";
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function scoreColor(score) {
  if (score >= 8) return "var(--green)";
  if (score >= 6) return "var(--yellow)";
  return "var(--red)";
}

function verdictBadge(verdict) {
  const map = {
    "Strongly Recommend": "badge-green",
    "Recommend": "badge-green",
    "Maybe": "badge-yellow",
    "Not Recommended": "badge-red",
  };
  return map[verdict] || "badge-purple";
}
