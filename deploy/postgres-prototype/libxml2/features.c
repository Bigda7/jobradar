/* Query the stable public ABI without installing additional development packages. */
#include <stdio.h>
extern int xmlHasFeature(int feature);

int main(void) {
    int feature;
    for (feature = 1; feature <= 64; feature++)
        printf("%d=%d\n", feature, xmlHasFeature(feature));
    return 0;
}
