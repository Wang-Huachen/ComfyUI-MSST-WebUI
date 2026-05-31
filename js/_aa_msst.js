// ComfyUI-MSST-WebUI: 级联筛选 + 动态输出端口
// 文件名 _aa_msst.js 确保在其它扩展前加载
(function() {
    "use strict";

    function _getExtensionPath() {
        var scripts = document.getElementsByTagName("script");
        for (var i = 0; i < scripts.length; i++) {
            var src = scripts[i].src || "";
            var m = src.match(/\/extensions\/([^/]+)\//);
            if (m && m[1].toLowerCase().indexOf("msst") >= 0) {
                return "/extensions/" + m[1];
            }
        }
        return "/extensions/ComfyUI-MSST-WebUI";
    }

    var MSST_NODE_NAMES = ["MSSTSeparate", "UVRSeparate"];
    var MODEL_DATA_URL = _getExtensionPath() + "/model_data.json";

    // ── 同步加载模型数据 ──
    var modelData = null;
    try {
        var _xhr = new XMLHttpRequest();
        _xhr.open("GET", MODEL_DATA_URL, false);
        _xhr.send();
        if (_xhr.status === 200) modelData = JSON.parse(_xhr.responseText);
    } catch (_e) {}

    function _redraw(node) {
        node.setDirtyCanvas(true, true);
        if (node.graph) node.graph.setDirtyCanvas(true, true);
    }
    function _resize(node) {
        if (node.setSize && node.computeSize) node.setSize(node.computeSize());
        if (node.graph && node.graph.change) node.graph.change();
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

    // ── 输出同步（新格式: [0]=model_info, [1]=AUDIO_0, [2]=STRING_0, ...） ──
    function _syncOutputs(node) {
        if (!node.outputs || !node.outputs.length) return;
        var nW = node.widgets && node.widgets.find(function(w) { return w.name === "model_name"; });
        var name = nW ? nW.value : "";
        var stems = modelData && modelData[name] ? modelData[name].instruments : null;
        if (!stems || !stems.length) return;

        var want = stems.length;
        var have = (node.outputs.length - 1) / 2;
        if (have < 0 || have !== Math.floor(have)) return;

        if (want === have) {
            // 只更新名称
        } else if (want < have) {
            // 收缩
            for (var i = have - 1; i >= want; i--) {
                var s = 1 + i * 2 + 1, a = 1 + i * 2;
                if (s < node.outputs.length) node.removeOutput(s);
                if (a < node.outputs.length) node.removeOutput(a);
            }
        } else {
            // 扩展
            for (var i = have; i < want; i++) {
                node.addOutput(stems[i], "AUDIO");
                node.addOutput(stems[i] + "_fn", "STRING");
            }
        }

        // 重命名
        if (node.outputs[0]) {
            node.outputs[0].name = "model_info";
            node.outputs[0].label = "model_info";
            node.outputs[0].type = "STRING";
        }
        for (var i = 0; i < want; i++) {
            var a = 1 + i * 2, s = a + 1;
            if (node.outputs[a]) {
                node.outputs[a].name = stems[i];
                node.outputs[a].label = stems[i];
                node.outputs[a].type = "AUDIO";
            }
            if (node.outputs[s]) {
                node.outputs[s].name = stems[i] + "_fn";
                node.outputs[s].label = stems[i] + "_fn";
                node.outputs[s].type = "STRING";
            }
        }
        _resize(node);
        _redraw(node);
    }

    // ── 注册扩展 ──
    function _doRegister(appInstance) {
        if (!appInstance || !appInstance.registerExtension) return;
        appInstance.registerExtension({
            name: "ComfyUI.MSSTWebUI",

            nodeCreated: function(node) {
                if (MSST_NODE_NAMES.indexOf(node.comfyClass) < 0) return;

                // model_name 回调
                var nW = node.widgets && node.widgets.find(function(w) { return w.name === "model_name"; });
                if (nW) {
                    var cb = nW.callback;
                    nW.callback = function(v) {
                        try { if (cb) cb.call(this, v); } catch(e) {}
                        _syncOutputs(node);
                    };
                }

                // model_category 回调
                if (node.comfyClass === "MSSTSeparate") {
                    var cW = node.widgets && node.widgets.find(function(w) { return w.name === "model_category"; });
                    if (cW) {
                        var cc = cW.callback;
                        cW.callback = function(v) {
                            try { if (cc) cc.call(this, v); } catch(e) {}
                            _filterModels(node);
                            _syncOutputs(node);
                        };
                    }
                }

                // MSST: rAF 延迟（widget 值需等待恢复）
                // UVR:  同步执行（固定 2 轨，无需等待）
                if (node.comfyClass === "MSSTSeparate") {
                    requestAnimationFrame(function() {
                        _filterModels(node);
                        _syncOutputs(node);
                    });
                } else {
                    _syncOutputs(node);
                }
            }
        });
    }

    var _app = window.app;
    try {
        Object.defineProperty(window, 'app', {
            get: function() { return _app; },
            set: function(v) { _app = v; _doRegister(v); },
            configurable: true
        });
    } catch(_e) {
        var _poll = function() {
            if (typeof app !== "undefined" && app.registerExtension) {
                _doRegister(app);
            } else {
                setTimeout(_poll, 0);
            }
        };
        _poll();
    }
    if (_app) _doRegister(_app);
})();
