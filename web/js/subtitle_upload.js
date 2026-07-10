import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "ComfyUI.Subtitle.NodeUi",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "TencentSubtitleBurn") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            for (const widgetName of ["local_video", "need_wordlist", "adapt_words", "background_alpha"]) {
                const widget = this.widgets?.find((item) => item.name === widgetName);
                if (!widget) continue;
                widget.computeSize = () => [0, -4];
                widget.type = "hidden";
            }
            return result;
        };
    },
});
