
NVCC ?= nvcc
CXXFLAGS ?= -O3 -std=c++17
CUDAFLAGS ?= -O3 -std=c++17 -arch=native --use_fast_math -lineinfo
TARGET := hash256-cuda
SRC := hash256_cuda.cu

all: $(TARGET)

$(TARGET): $(SRC)
	$(NVCC) $(CUDAFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)
