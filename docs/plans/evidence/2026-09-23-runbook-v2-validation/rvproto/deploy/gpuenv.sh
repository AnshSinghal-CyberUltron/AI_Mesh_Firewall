# Source me: puts the pip-installed CUDA 13 / cuDNN 9 / TensorRT 10 libs on the loader path
# for ONNX Runtime's TensorRT and CUDA execution providers.
RV=${RV:-$HOME/rv}
SITE=$RV/venv/lib/python3.12/site-packages
LD=$SITE/tensorrt_libs
for d in "$SITE"/nvidia/*/lib "$SITE"/nvidia/cu13/lib; do [ -d "$d" ] && LD=$LD:$d; done
export LD_LIBRARY_PATH=$LD:${LD_LIBRARY_PATH:-}
