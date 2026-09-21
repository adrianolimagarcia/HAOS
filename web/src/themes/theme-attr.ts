/** Stamp the active dashboard theme name on <html> so CSS can scope liquid glass. */
export function setThemeDataset(name: string) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = name;
}
