#include <cstdio>

#define TWICE(x) ((x) + (x))

static int clamp(int value, int high) {
  if (value > high) { return high; } else { return value; }
}

static int never_called(int value) {
  return value * 3;
}

#if 0
static int removed(int value) {
  return value - 1;
}
#endif

template <typename T> T doubled(T value) { return TWICE(value); }

int main(int argc, char **argv) {
  (void)argv;
  int total = 0;
  for (int i = 0; i < argc; ++i) {
    total += clamp(doubled(i), 10);
  }
  if (argc > 5)
    total += never_called(argc);
#if 0
  total += removed(argc);
  total -= 1;
#endif
  std::printf("%d\n", total);
  return total > 100 ? 1 : 0;
}
