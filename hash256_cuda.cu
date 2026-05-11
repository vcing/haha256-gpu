
#include <cuda_runtime.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <chrono>
#include <string>
#include <vector>
#include <iostream>
#include <iomanip>
#include <algorithm>
#include <random>

#define CUDA_CHECK(call) do { \
    cudaError_t err__ = (call); \
    if (err__ != cudaSuccess) { \
        fprintf(stderr, "CUDA error %s:%d: %s\n", __FILE__, __LINE__, cudaGetErrorString(err__)); \
        exit(1); \
    } \
} while (0)

struct FoundResult {
    int flag;
    unsigned long long counter;
    unsigned char nonce[32];
    unsigned char hash[32];
};

__host__ __device__ static inline uint64_t rotl64(uint64_t x, int s) {
    return (x << s) | (x >> (64 - s));
}

__host__ __device__ static inline uint64_t load64_le(const unsigned char* p) {
    uint64_t x = 0;
    #pragma unroll
    for (int i = 0; i < 8; i++) x |= ((uint64_t)p[i]) << (8 * i);
    return x;
}

__host__ __device__ static inline void store64_le(unsigned char* p, uint64_t x) {
    #pragma unroll
    for (int i = 0; i < 8; i++) p[i] = (unsigned char)((x >> (8 * i)) & 0xff);
}

__constant__ uint64_t d_keccakf_rndc[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

static const uint64_t h_keccakf_rndc[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

__host__ __device__ static inline uint64_t round_const(int i) {
#ifdef __CUDA_ARCH__
    return d_keccakf_rndc[i];
#else
    return h_keccakf_rndc[i];
#endif
}

__host__ __device__ void keccakf1600(uint64_t st[25]) {
    const int rho[24] = {1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44};
    const int pi[24]  = {10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1};

    for (int round = 0; round < 24; round++) {
        uint64_t bc[5];
        #pragma unroll
        for (int i = 0; i < 5; i++) bc[i] = st[i] ^ st[i+5] ^ st[i+10] ^ st[i+15] ^ st[i+20];
        #pragma unroll
        for (int i = 0; i < 5; i++) {
            uint64_t t = bc[(i + 4) % 5] ^ rotl64(bc[(i + 1) % 5], 1);
            st[i] ^= t; st[i+5] ^= t; st[i+10] ^= t; st[i+15] ^= t; st[i+20] ^= t;
        }

        uint64_t t = st[1];
        #pragma unroll
        for (int i = 0; i < 24; i++) {
            int j = pi[i];
            uint64_t tmp = st[j];
            st[j] = rotl64(t, rho[i]);
            t = tmp;
        }

        #pragma unroll
        for (int j = 0; j < 25; j += 5) {
            uint64_t a0 = st[j], a1 = st[j+1], a2 = st[j+2], a3 = st[j+3], a4 = st[j+4];
            st[j]   = a0 ^ ((~a1) & a2);
            st[j+1] = a1 ^ ((~a2) & a3);
            st[j+2] = a2 ^ ((~a3) & a4);
            st[j+3] = a3 ^ ((~a4) & a0);
            st[j+4] = a4 ^ ((~a0) & a1);
        }

        st[0] ^= round_const(round);
    }
}

__host__ __device__ void keccak256_oneblock(const unsigned char* msg, int len, unsigned char out[32]) {
    uint64_t st[25];
    #pragma unroll
    for (int i = 0; i < 25; i++) st[i] = 0;
    for (int i = 0; i < len; i++) {
        int lane = i >> 3;
        int off = (i & 7) << 3;
        st[lane] ^= ((uint64_t)msg[i]) << off;
    }
    // Ethereum Keccak padding: suffix 0x01, final bit 0x80 at rate-1. Rate for keccak256 is 136 bytes.
    st[len >> 3] ^= ((uint64_t)0x01) << ((len & 7) << 3);
    st[135 >> 3] ^= ((uint64_t)0x80) << ((135 & 7) << 3);
    keccakf1600(st);
    #pragma unroll
    for (int i = 0; i < 4; i++) store64_le(out + 8*i, st[i]);
}

__device__ static inline bool be32_lt(const unsigned char a[32], const unsigned char b[32]) {
    #pragma unroll
    for (int i = 0; i < 32; i++) {
        if (a[i] < b[i]) return true;
        if (a[i] > b[i]) return false;
    }
    return false;
}

__device__ static inline void store_u64_be(unsigned char* p, uint64_t x) {
    #pragma unroll
    for (int i = 0; i < 8; i++) p[i] = (unsigned char)((x >> (56 - 8*i)) & 0xff);
}

__global__ void mine_kernel(const unsigned char* challenge, const unsigned char* target,
                            const unsigned char* prefix24, uint64_t start_counter,
                            uint64_t iterations_per_thread, FoundResult* found) {
    uint64_t tid = (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    uint64_t stride = (uint64_t)gridDim.x * blockDim.x;
    unsigned char input[64];
    unsigned char hash[32];

    #pragma unroll
    for (int i = 0; i < 32; i++) input[i] = challenge[i];
    #pragma unroll
    for (int i = 0; i < 24; i++) input[32+i] = prefix24[i];

    for (uint64_t k = 0; k < iterations_per_thread; k++) {
        if (found->flag) return;
        uint64_t counter = start_counter + tid + k * stride;
        store_u64_be(input + 56, counter);
        keccak256_oneblock(input, 64, hash);
        if (be32_lt(hash, target)) {
            if (atomicCAS(&(found->flag), 0, 1) == 0) {
                found->counter = counter;
                #pragma unroll
                for (int i = 0; i < 24; i++) found->nonce[i] = prefix24[i];
                store_u64_be(found->nonce + 24, counter);
                #pragma unroll
                for (int i = 0; i < 32; i++) found->hash[i] = hash[i];
            }
            return;
        }
    }
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static bool parse_hex_bytes(std::string s, unsigned char* out, size_t n) {
    if (s.rfind("0x", 0) == 0 || s.rfind("0X", 0) == 0) s = s.substr(2);
    if (s.size() != n * 2) return false;
    for (size_t i = 0; i < n; i++) {
        int hi = hexval(s[2*i]), lo = hexval(s[2*i+1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (unsigned char)((hi << 4) | lo);
    }
    return true;
}

static std::string hex_bytes(const unsigned char* p, size_t n) {
    static const char* h = "0123456789abcdef";
    std::string s = "0x";
    s.reserve(2 + 2*n);
    for (size_t i = 0; i < n; i++) { s.push_back(h[p[i] >> 4]); s.push_back(h[p[i] & 15]); }
    return s;
}

static bool selftest() {
    struct TV { const char* msg; int len; const char* want; } tvs[] = {
        {"", 0, "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"},
        {"abc", 3, "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"}
    };
    for (auto &tv: tvs) {
        unsigned char out[32];
        keccak256_oneblock((const unsigned char*)tv.msg, tv.len, out);
        std::string got = hex_bytes(out, 32);
        if (got != tv.want) {
            std::cerr << "selftest failed: got " << got << " want " << tv.want << "\n";
            return false;
        }
    }
    return true;
}

static void usage(const char* argv0) {
    fprintf(stderr,
        "Usage: %s --benchmark --challenge 0x<32B> --target 0x<32B> [options]\n"
        "Options:\n"
        "  --device N              CUDA device (default 0)\n"
        "  --seconds N             benchmark duration approx seconds (default 10)\n"
        "  --blocks N              CUDA blocks (default SMs*32)\n"
        "  --threads N             threads/block (default 256)\n"
        "  --iters N               iterations/thread per launch (default 256)\n"
        "  --prefix 0x<24B>        nonce prefix (default random; fixed only for debugging)\n"
        "  --start N               low counter start (default 0)\n"
        "  --selftest              run Keccak selftest\n",
        argv0);
}

int main(int argc, char** argv) {
    std::string challenge_hex, target_hex, prefix_hex;
    int device = 0, seconds = 10, blocks = 0, threads = 256;
    uint64_t iters = 256, start = 0;
    bool benchmark = false, do_selftest = false;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto need = [&](const char* name){ if (i+1 >= argc) { fprintf(stderr, "missing value for %s\n", name); exit(2);} return std::string(argv[++i]); };
        if (a == "--benchmark") benchmark = true;
        else if (a == "--selftest") do_selftest = true;
        else if (a == "--challenge") challenge_hex = need("--challenge");
        else if (a == "--target") target_hex = need("--target");
        else if (a == "--prefix") prefix_hex = need("--prefix");
        else if (a == "--device") device = std::stoi(need("--device"));
        else if (a == "--seconds") seconds = std::stoi(need("--seconds"));
        else if (a == "--blocks") blocks = std::stoi(need("--blocks"));
        else if (a == "--threads") threads = std::stoi(need("--threads"));
        else if (a == "--iters") iters = std::stoull(need("--iters"));
        else if (a == "--start") start = std::stoull(need("--start"));
        else { usage(argv[0]); return 2; }
    }
    if (do_selftest) {
        bool ok = selftest();
        std::cout << (ok ? "selftest ok" : "selftest failed") << "\n";
        if (!ok) return 1;
        if (!benchmark) return 0;
    } else if (!selftest()) return 1;

    if (!benchmark || challenge_hex.empty() || target_hex.empty()) { usage(argv[0]); return 2; }

    unsigned char h_challenge[32], h_target[32], h_prefix[24];
    if (!parse_hex_bytes(challenge_hex, h_challenge, 32)) { fprintf(stderr, "bad challenge\n"); return 2; }
    if (!parse_hex_bytes(target_hex, h_target, 32)) { fprintf(stderr, "bad target\n"); return 2; }
    if (!prefix_hex.empty()) {
        if (!parse_hex_bytes(prefix_hex, h_prefix, 24)) { fprintf(stderr, "bad prefix\n"); return 2; }
    } else {
        std::random_device rd;
        for (int i = 0; i < 24; i++) h_prefix[i] = (unsigned char)(rd() & 0xff);
    }

    CUDA_CHECK(cudaSetDevice(device));
    cudaDeviceProp prop{};
    CUDA_CHECK(cudaGetDeviceProperties(&prop, device));
    if (blocks <= 0) blocks = prop.multiProcessorCount * 32;
    std::cerr << "device=" << device << " name=" << prop.name << " sms=" << prop.multiProcessorCount
              << " blocks=" << blocks << " threads=" << threads << " iters=" << iters << "\n";

    unsigned char *d_challenge, *d_target, *d_prefix;
    FoundResult *d_found, h_found{};
    CUDA_CHECK(cudaMalloc(&d_challenge, 32));
    CUDA_CHECK(cudaMalloc(&d_target, 32));
    CUDA_CHECK(cudaMalloc(&d_prefix, 24));
    CUDA_CHECK(cudaMalloc(&d_found, sizeof(FoundResult)));
    CUDA_CHECK(cudaMemcpy(d_challenge, h_challenge, 32, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(d_target, h_target, 32, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(d_prefix, h_prefix, 24, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemset(d_found, 0, sizeof(FoundResult)));

    uint64_t per_launch = (uint64_t)blocks * (uint64_t)threads * iters;
    uint64_t total = 0;
    auto t0 = std::chrono::steady_clock::now();
    int launches = 0;
    while (true) {
        mine_kernel<<<blocks, threads>>>(d_challenge, d_target, d_prefix, start + total, iters, d_found);
        CUDA_CHECK(cudaGetLastError());
        CUDA_CHECK(cudaDeviceSynchronize());
        launches++;
        total += per_launch;
        CUDA_CHECK(cudaMemcpy(&h_found, d_found, sizeof(FoundResult), cudaMemcpyDeviceToHost));
        auto now = std::chrono::steady_clock::now();
        double elapsed = std::chrono::duration<double>(now - t0).count();
        if (h_found.flag || elapsed >= seconds) {
            double hps = total / elapsed;
            std::cout << "{\n";
            std::cout << "  \"device\": \"" << prop.name << "\",\n";
            std::cout << "  \"elapsed_sec\": " << std::fixed << std::setprecision(6) << elapsed << ",\n";
            std::cout << "  \"hashes\": " << total << ",\n";
            std::cout << "  \"hashrate_hps\": " << std::fixed << std::setprecision(0) << hps << ",\n";
            std::cout << "  \"hashrate_ghps\": " << std::fixed << std::setprecision(3) << (hps / 1e9) << ",\n";
            std::cout << "  \"prefix\": \"" << hex_bytes(h_prefix, 24) << "\",\n";
            std::cout << "  \"launches\": " << launches << ",\n";
            std::cout << "  \"found\": " << (h_found.flag ? "true" : "false");
            if (h_found.flag) {
                std::cout << ",\n  \"counter\": " << h_found.counter << ",\n";
                std::cout << "  \"nonce\": \"" << hex_bytes(h_found.nonce, 32) << "\",\n";
                std::cout << "  \"hash\": \"" << hex_bytes(h_found.hash, 32) << "\"\n";
            } else {
                std::cout << "\n";
            }
            std::cout << "}\n";
            break;
        }
    }
    cudaFree(d_challenge); cudaFree(d_target); cudaFree(d_prefix); cudaFree(d_found);
    return 0;
}
