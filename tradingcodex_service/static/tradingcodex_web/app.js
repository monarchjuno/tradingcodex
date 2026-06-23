(() => {
  const scrollKey = "tcxWebScrollY";
  const pathKey = "tcxWebScrollPath";
  const shellSelector = ".tc-main-shell";
  const statefulLinks = "a.tc-agent-chip, a.tc-segmented-control a, a.tc-skill-link";

  const shell = () => document.querySelector(shellSelector);

  const saveScroll = () => {
    const target = shell();
    if (!target) return;
    sessionStorage.setItem(scrollKey, String(target.scrollTop));
    sessionStorage.setItem(pathKey, window.location.pathname);
  };

  const applyAfterLayout = (callback) => {
    callback();
    requestAnimationFrame(callback);
    setTimeout(callback, 40);
    setTimeout(callback, 140);
  };

  const clearSavedScroll = () => {
    sessionStorage.removeItem(scrollKey);
    sessionStorage.removeItem(pathKey);
  };

  const scrollToHashTarget = () => {
    if (!window.location.hash) return false;
    const target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    const scrollShell = shell();
    if (!target || !scrollShell) return false;

    applyAfterLayout(() => {
      const shellRect = scrollShell.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      scrollShell.scrollTop += targetRect.top - shellRect.top - 10;
    });
    clearSavedScroll();
    return true;
  };

  const restoreScroll = () => {
    const saved = sessionStorage.getItem(scrollKey);
    const savedPath = sessionStorage.getItem(pathKey);
    if (!saved) return;
    if (savedPath && savedPath !== window.location.pathname) {
      clearSavedScroll();
      return;
    }

    const y = Number(saved);
    if (!Number.isFinite(y)) {
      clearSavedScroll();
      return;
    }

    applyAfterLayout(() => {
      const target = shell();
      if (target) target.scrollTop = y;
    });
    setTimeout(clearSavedScroll, 140);
  };

  window.addEventListener("DOMContentLoaded", () => {
    if (!scrollToHashTarget()) restoreScroll();
  });
  window.addEventListener("pagehide", saveScroll);
  window.addEventListener("beforeunload", saveScroll);
  document.addEventListener("click", (event) => {
    const link = event.target.closest(statefulLinks);
    if (link) saveScroll();
  });
})();
