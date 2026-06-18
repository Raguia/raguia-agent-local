(async () => {
  if (!window.__TAURI__) {
    document.body.replaceChildren();
    const message = document.createElement("div");
    message.className = "runtime-error";
    message.textContent =
      "Cette page doit être ouverte depuis l'application Raguia Agent.";
    document.body.appendChild(message);
    return;
  }

  const { invoke } = window.__TAURI__.core;
  const { open: dialogOpen } = window.__TAURI__.dialog;

  const form = document.getElementById("wizardForm");
  const connectBtn = document.getElementById("connectBtn");
  const messageEl = document.getElementById("message");
  const browseBtn = document.getElementById("browseBtn");
  const watchDir = document.getElementById("watchDir");

  try {
    const home = await invoke("get_home_dir");
    const os = await invoke("get_os_kind");
    const sep = os === "windows" ? "\\" : "/";
    watchDir.value = home + sep + "Documents" + sep + "RAGUIA";
  } catch {
    watchDir.value = "~/Documents/RAGUIA";
  }

  browseBtn.addEventListener("click", async () => {
    try {
      const selected = await dialogOpen({
        directory: true,
        multiple: false,
        title: "Choisir le dossier de synchronisation",
      });
      if (selected) {
        watchDir.value = selected;
      }
    } catch (e) {
      console.error("Folder picker failed:", e);
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setMessage("", "");

    const apiUrl = document.getElementById("apiUrl").value.trim();
    const slug = document.getElementById("slug").value.trim();
    const password = document.getElementById("password").value.trim();
    const watchDirVal = watchDir.value.trim();

    if (!apiUrl || !slug || !password) {
      setMessage("Veuillez remplir tous les champs obligatoires.", "error");
      return;
    }

    if (!/^https?:\/\/.+/.test(apiUrl)) {
      setMessage(
        "URL du portail invalide. Elle doit commencer par http:// ou https://",
        "error",
      );
      return;
    }

    setLoading(true);

    try {
      await invoke("login", {
        slug,
        password,
        apiUrl,
        watchDir: watchDirVal || null,
      });

      setMessage("Connexion réussie ! Démarrage de l'agent…", "success");

      setTimeout(async () => {
        try {
          await window.__TAURI__.window.getCurrent().close();
        } catch {
          window.close();
        }
      }, 1200);
    } catch (err) {
      setMessage(
        typeof err === "string" ? err : err.message || "Erreur de connexion",
        "error",
      );
    } finally {
      setLoading(false);
    }
  });

  function setLoading(loading) {
    connectBtn.classList.toggle("loading", loading);
    connectBtn.disabled = loading;
  }

  function setMessage(text, type) {
    messageEl.textContent = text;
    messageEl.className = "message" + (type ? " " + type : "");
  }
})();
