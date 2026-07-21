import { app } from "../../../scripts/app.js";

function hideWidget(widget) {
    if (!widget) return;

    widget.computeSize = () => [0, -4];
    widget.draw = () => {};
    widget.type = "converted-widget";
    widget.hidden = true;

    for (const element of [widget.element, widget.inputEl]) {
        if (element?.style) element.style.display = "none";
    }
}

app.registerExtension({
    name: "ComfyUI.Subtitle.NodeUi",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "TencentSubtitleBurn") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            for (const widgetName of ["local_video", "need_wordlist", "adapt_words", "background_alpha"]) {
                hideWidget(this.widgets?.find((item) => item.name === widgetName));
            }
            // Existing workflows persist their old node height. Recalculate it
            // after hiding compatibility-only widgets so no blank area remains.
            requestAnimationFrame(() => {
                this.setSize(this.computeSize());
                this.setDirtyCanvas(true, true);
            });
            return result;
        };
    },
});
