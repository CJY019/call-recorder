# 第三方组件与许可证

本文件记录当前源码声明的直接运行依赖，以及发布安装包时需要核对的许可证。它不是这些许可证的全文，也不能证明某个安装包已经包含了完整的第三方授权材料。**发布 Windows 安装包前，必须检查最终实际打包的组件，并将它们随包附带的完整许可证文本和必要版权声明一并放入分发物。**

## 当前直接依赖

### PyAudioWPatch 0.2.12.9

call_recorder/requirements.txt 固定使用 PyAudioWPatch 0.2.12.9。其发行元数据标示 Apache-2.0；上游源代码同时说明，基于它的 PyAudio 代码按 MIT License 发布，并使用 PortAudio v19。最终安装包应按实际 wheel 和二进制组件核对，并附带 Apache-2.0、PyAudio 的 MIT 条款及 PortAudio v19 的 MIT 许可证文本和所需声明。

- [PyAudioWPatch 上游仓库及许可证文件](https://github.com/s0d3s/PyAudioWPatch)
- [PyAudioWPatch 0.2.12.9 发行记录](https://pypi.org/project/PyAudioWPatch/0.2.12.9/)
- [PortAudio v19 许可证](https://portaudio.com/license.html)

### NumPy

call_recorder/requirements.txt 声明 NumPy >=1.26,<3。NumPy 主项目使用 BSD-3-Clause。NumPy 的预编译 wheel 还包含独立组件及其许可证，例如 OpenBLAS 的 BSD-3-Clause 与 BSD-3-Clause-Attribution 条款、GNU Fortran runtime 的 GPL-3.0-with-GCC-exception 条款；安装包也可能包含其他上游组件的许可证文件。由于实际内容随 NumPy 版本、平台和构建方式而变，打包时应锁定实际版本、检查所用 wheel 随附的许可证目录和二进制清单，并将对应的完整文本和声明纳入安装包。

- [NumPy 官方许可证说明及 wheel 组件清单](https://github.com/numpy/numpy/blob/main/doc/source/license.rst)
- [NumPy 官方构建元数据与许可证文件列表](https://github.com/numpy/numpy/blob/main/pyproject.toml)

## 发布打包检查

1. 以最终构建环境和安装包为准，记录实际捆绑的依赖、二进制和各自版本；当前 requirements 文件不包含安装器或冻结应用可能引入的全部组件。
2. 将各组件原始分发物中的完整许可证文本、版权声明及需要保留的通知随安装包提供。仅保留本文件或写出许可证名称不足以替代这些文本。
3. 如果最终安装包包含 Python runtime、打包工具或其他额外组件，也要按其实际版本纳入许可证核对，并在随包材料中补齐相应声明。

项目自身代码的 MIT License 见 [LICENSE](LICENSE)。

