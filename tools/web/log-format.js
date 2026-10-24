customElements.whenDefined("esp-log").then(() => {
  const prototype = customElements.get("esp-log").prototype;
  const render = prototype.render;
  const ansiStyle = /\x1b\[[0-9;:]*m/g;

  prototype.render = function () {
    for (const entry of this.logs) {
      entry.tag = entry.tag.replace(ansiStyle, "");
      entry.detail = entry.detail.replace(ansiStyle, "");
    }
    return render.call(this);
  };
});