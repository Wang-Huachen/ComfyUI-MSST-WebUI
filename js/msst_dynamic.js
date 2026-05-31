// ComfyUI-MSST-WebUI: 级联筛选 + Canvas 绘制音轨名
(function() {
    "use strict";

    var MSST_NODE_NAMES = ["MSSTSeparate", "UVRSeparate"];
    var MODEL_DATA_URL = "/extensions/ComfyUI-MSST-WebUI/model_data.json";
    var modelData = null;

    // ── 触发重绘 ──
    function _redraw(node) {
        node.setDirtyCanvas(true, true);
        if (node.graph) node.graph.setDirtyCanvas(true, true);
    }

    // ── 级联筛选 ──
    function _filterModels(node) {
        var cW = node.widgets && node.widgets.find(function(w) { return w.name === "model_category"; });
        var nW = node.widgets && node.widgets.find(function(w) { return w.name === "model_name"; });
        if (!cW || !nW || !modelData) return;
        if (!nW._all) nW._all = nW.options.values.slice();
        var filtered = nW._all.filter(function(n) {
            var d = modelData[n];
            return d && d.category === cW.value;
        });
        nW.options.values = filtered;
        if (filtered.length > 0) {
            if (filtered.indexOf(nW.value) < 0) nW.value = filtered[0];
        } else {
            nW.value = "";
        }
        _redraw(node);
    }

    // ── 刷新所有节点 ──
    function _refreshAll() {
        if (!app || !app.graph || !app.graph.nodes) return;
        app.graph.nodes.forEach(function(node) {
            if (MSST_NODE_NAMES.indexOf(node.comfyClass) >= 0) {
                if (node.comfyClass === "MSSTSeparate") _filterModels(node);
                _redraw(node);
            }
        });
    }

    // ── 加载模型数据 ──
    function _loadData() {
        if (modelData) return;
        fetch(MODEL_DATA_URL).then(function(r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        }).then(function(data) {
            modelData = data;
            _refreshAll();
        }).catch(function(e) {
            setTimeout(_loadData, 5000);
        });
    }

    // ── 注册扩展 ──
    function _register() {
        if (typeof app === "undefined") {
            setTimeout(_register, 100);
            return;
        }
        app.registerExtension({
            name: "ComfyUI.MSSTWebUI",

            nodeCreated: function(node) {
                if (MSST_NODE_NAMES.indexOf(node.comfyClass) < 0) return;

                // ── Canvas 绘制音轨名（输出端口左侧） ──
                // 每次绘制时实时查询，避免缓存失效
                node.onDrawForeground = function(ctx) {
                    var node = this;
                    var nW = node.widgets && node.widgets.find(function(w) { return w.name === "model_name"; });
                    var modelName = nW ? nW.value : "";
                    var stems = null;
                    if (modelData && modelData[modelName]) {
                        stems = modelData[modelName].instruments;
                    }
                    if (!stems || !stems.length) return;
                    if (!node.outputs) return;
                    ctx.save();
                    ctx.font = "10px sans-serif";
                    ctx.fillStyle = "#6af";
                    ctx.textAlign = "right";
                    for (var i = 0; i < stems.length && i < 6; i++) {
                        var idx = i * 2;
                        if (node.outputs[idx]) {
                            var pos = node.getOutputPos(idx);
                            if (pos) {
                                ctx.fillText(stems[i], pos[0] - node.pos[0] - 80, pos[1] - node.pos[1] + 3);
                            }
                        }
                    }
                    ctx.restore();
                };

                // ── model_name 变化 → 更新 ──
                var nW = node.widgets && node.widgets.find(function(w) { return w.name === "model_name"; });
                if (nW) {
                    var cb = nW.callback;
                    nW.callback = function(v) {
                        try { if (cb) cb.call(this, v); } catch(e) {}
                        _redraw(node);
                    };
                }

                // ── model_category 变化 → 级联 ──
                if (node.comfyClass === "MSSTSeparate") {
                    var cW = node.widgets && node.widgets.find(function(w) { return w.name === "model_category"; });
                    if (cW) {
                        var cc = cW.callback;
                        cW.callback = function(v) {
                            try { if (cc) cc.call(this, v); } catch(e) {}
                            _filterModels(node);
                        };
                    }
                }

                // 初始化
                _redraw(node);
            }
        });

        _loadData();
        setInterval(function() {
            if (modelData) _refreshAll();
        }, 3000);
    }

    _register();
})();
