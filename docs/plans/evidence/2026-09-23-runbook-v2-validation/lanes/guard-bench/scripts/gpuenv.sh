# source me: puts pip-installed CUDA13/cuDNN/TensorRT10 libs on the loader path for ORT's TRT/CUDA EPs
SITE=$HOME/venv/lib/python3.12/site-packages
LD=$SITE/tensorrt_libs
for d in $SITE/nvidia/*/lib $SITE/nvidia/cu13/lib; do [ -d "$d" ] && LD=$LD:$d; done
export LD_LIBRARY_PATH=$LD:${LD_LIBRARY_PATH:-}
export PATH=$HOME/venv/bin:$PATH
