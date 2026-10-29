# Plexa mobility: first two baselines

Every road/direction has equal weight. Within each subgroup this weighting is recomputed using only that subgroup's rows.

Volumes, errors and interval widths are vehicles/hour. Log metrics use log1p(volume). Coverage is a proportion; the target is 0.80. WIS combines the median and central 80% interval, and penalizes both interval width and missed observations.

- These are four independent benchmark experiments. No test data were used to choose parameters.
- B1 uses road-balanced empirical q10/q50/q90 by weekday/hour, with hour and city fallbacks.
- B2 uses log1p volume: borough/lane-group profile shrunk toward B1, a road level, and an observed-hour shape pooled across weekdays.
- Fixed priors: group20 road/direction units, road level2 observed dates, hourly shape3 observed dates. These values were not tuned against test scores.
- B2 q10/q50/q90 use signed log-residual quantiles from three grouped out-of-fold training fits. Residuals are fitted by hour with shrinkage toward pooled residual quantiles.
- Known-road calibration holds out whole segment/date groups while retaining road history; cold-start calibration withholds whole segments.
- Empirical 80% prediction intervals have no formal coverage guarantee. OOF fits use less data than final fits; calibration may not transfer across years or seasons.
- Time-test training ends in2018; test starts in2019 and includes later COVID-era changes.
- Season transfer uses17 road/direction/year pairs (15 segments), full autumn versus same-year full summer days. It is a small retrospective sensitivity test, not a future forecast.
- All road_type values are unspecified. The first two baselines do not use source, holiday or year as predictors; those remain available for the boosted baseline.
- Subgroup metrics recompute equal-road weights within each subgroup; do not average subgroup scores.

## Overall test results

| scenario | baseline | n_rows | n_roads | n_segments | mae | rmse | log1p_mae | log1p_rmse | coverage_80 | mean_interval_width | pinball_10 | pinball_50 | pinball_90 | interval_score_80 | weighted_interval_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| within_road | B1_global | 312488 | 4671 | 3453 | 325.8331 | 710.2669 | 0.8728 | 1.1715 | 0.8000 | 935.8031 | 42.8490 | 162.9166 | 159.9417 | 2,027.9068 | 243.8048 |
| within_road | B2_road_scaled | 312488 | 4671 | 3453 | 117.1952 | 289.5928 | 0.2843 | 0.4340 | 0.8561 | 383.4910 | 25.3054 | 58.5976 | 31.1372 | 564.4262 | 76.6935 |
| unseen_roads | B1_global | 281864 | 1175 | 892 | 352.6527 | 764.0875 | 0.9364 | 1.2588 | 0.7766 | 872.6518 | 45.5823 | 176.3263 | 173.4630 | 2,190.4529 | 263.5811 |
| unseen_roads | B2_road_scaled | 281864 | 1175 | 892 | 314.2132 | 680.9527 | 0.8095 | 1.1110 | 0.7887 | 834.9208 | 43.9940 | 157.1066 | 131.8528 | 1,758.4683 | 221.9689 |
| time | B1_global | 258867 | 1154 | 775 | 196.0119 | 367.2413 | 0.7050 | 0.9247 | 0.8134 | 691.4800 | 28.1347 | 98.0059 | 77.1385 | 1,052.7317 | 135.5194 |
| time | B2_road_scaled | 258867 | 1154 | 775 | 102.9319 | 238.5767 | 0.3491 | 0.5058 | 0.6633 | 235.7298 | 22.3963 | 51.4660 | 32.5712 | 549.6751 | 70.9557 |
| season_transfer | B1_global | 3624 | 17 | 15 | 269.6499 | 528.1893 | 0.7861 | 1.0180 | 0.7632 | 760.7467 | 35.8052 | 134.8250 | 126.3318 | 1,621.3695 | 197.9746 |
| season_transfer | B2_road_scaled | 3624 | 17 | 15 | 124.4054 | 248.2540 | 0.3641 | 0.4892 | 0.9080 | 624.1020 | 24.4635 | 62.2027 | 44.3700 | 688.3348 | 87.3575 |

## Paired MAE comparison

Positive B1 minus B2 means B2 improves MAE. The 95% percentile intervals resample whole segments, keeping both directions together. A confidence interval crossing zero does not establish a consistent improvement.

| mae_b1 | mae_b2 | mae_improvement_b1_minus_b2 | ci95_lower | ci95_upper | n_bootstrap | seed | n_segments | n_roads | bootstrap_unit | ci_method | scenario | mae_reduction_percent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 325.8331 | 117.1952 | 208.6379 | 197.2773 | 220.2451 | 1000 | 42 | 3453 | 4671 | whole_segment | percentile | within_road | 64.0321 |
| 352.6527 | 314.2132 | 38.4395 | 29.7330 | 47.5764 | 1000 | 42 | 892 | 1175 | whole_segment | percentile | unseen_roads | 10.9001 |
| 196.0119 | 102.9319 | 93.0799 | 83.0715 | 104.2678 | 1000 | 42 | 775 | 1154 | whole_segment | percentile | time | 47.4869 |
| 269.6499 | 124.4054 | 145.2445 | 48.0572 | 272.5265 | 1000 | 42 | 15 | 17 | whole_segment | percentile | season_transfer | 53.8641 |

## Subgroup results

Every road/direction has equal weight. Within each subgroup this weighting is recomputed using only that subgroup's rows.

| scenario | baseline | group | group_value | n_rows | n_roads | n_segments | mae | rmse | log1p_mae | log1p_rmse | coverage_80 | mean_interval_width | pinball_10 | pinball_50 | pinball_90 | interval_score_80 | weighted_interval_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| within_road | B1_global | borough | <missing> | 2922 | 51 | 41 | 1,044.6969 | 1,659.1724 | 1.2957 | 1.6209 | 0.5523 | 934.2350 | 117.2661 | 522.3484 | 619.7431 | 7,370.0927 | 839.5718 |
| within_road | B1_global | borough | Bronx | 61773 | 917 | 647 | 304.1971 | 694.6665 | 0.8327 | 1.1275 | 0.8120 | 936.0908 | 39.7634 | 152.0985 | 158.2480 | 1,980.1133 | 233.4066 |
| within_road | B1_global | borough | Brooklyn | 86414 | 1317 | 981 | 243.7356 | 548.8921 | 0.7881 | 1.0627 | 0.8444 | 937.0211 | 33.2662 | 121.8678 | 122.5993 | 1,558.6548 | 185.1555 |
| within_road | B1_global | borough | Manhattan | 55644 | 756 | 619 | 506.8105 | 890.6322 | 1.0413 | 1.3029 | 0.7267 | 941.7346 | 64.6212 | 253.4053 | 210.5502 | 2,751.7138 | 352.3844 |
| within_road | B1_global | borough | Queens | 83711 | 1287 | 931 | 314.9660 | 726.6258 | 0.8803 | 1.2088 | 0.7984 | 932.1550 | 41.7846 | 157.4830 | 162.9371 | 2,047.2174 | 241.4698 |
| within_road | B1_global | borough | Staten Island | 22024 | 343 | 234 | 233.9026 | 533.2476 | 0.8429 | 1.1569 | 0.8010 | 931.2048 | 32.8336 | 116.9513 | 116.7002 | 1,495.3383 | 177.6568 |
| within_road | B1_global | source | automated | 97233 | 1996 | 1738 | 298.1074 | 623.5007 | 0.9392 | 1.2458 | 0.7776 | 930.6861 | 39.6500 | 149.0537 | 137.4001 | 1,770.5008 | 217.4025 |
| within_road | B1_global | source | historical | 215255 | 2883 | 1891 | 343.6699 | 758.0640 | 0.8230 | 1.1087 | 0.8180 | 940.6201 | 44.9461 | 171.8349 | 173.6310 | 2,185.7705 | 260.2747 |
| within_road | B1_global | road_type | unspecified | 312488 | 4671 | 3453 | 325.8331 | 710.2669 | 0.8728 | 1.1715 | 0.8000 | 935.8031 | 42.8490 | 162.9166 | 159.9417 | 2,027.9068 | 243.8048 |
| within_road | B1_global | training_days_bucket | 15-30 | 103218 | 774 | 522 | 216.3731 | 422.4706 | 0.7003 | 0.9411 | 0.8828 | 946.6609 | 31.9419 | 108.1865 | 97.4286 | 1,293.7050 | 158.3714 |
| within_road | B1_global | training_days_bucket | 3-7 | 102943 | 2573 | 2066 | 396.0355 | 851.3003 | 0.9636 | 1.2830 | 0.7558 | 928.1475 | 49.8237 | 198.0178 | 203.0425 | 2,528.6617 | 300.5893 |
| within_road | B1_global | training_days_bucket | 31+ | 10661 | 44 | 37 | 316.5554 | 570.7473 | 0.7601 | 0.9596 | 0.8631 | 942.8777 | 45.7093 | 158.2777 | 109.7896 | 1,554.9892 | 209.1844 |
| within_road | B1_global | training_days_bucket | 8-14 | 95666 | 1280 | 979 | 251.2234 | 514.8267 | 0.7986 | 1.0638 | 0.8364 | 944.3832 | 35.3257 | 125.6117 | 112.8273 | 1,481.5302 | 182.5098 |
| within_road | B1_global | covid | False | 256151 | 4176 | 3099 | 330.4696 | 715.2886 | 0.8721 | 1.1724 | 0.7995 | 934.7719 | 43.5966 | 165.2348 | 160.9253 | 2,045.2194 | 246.5045 |
| within_road | B1_global | covid | True | 56337 | 1111 | 796 | 234.7077 | 525.0867 | 0.7826 | 1.0474 | 0.8463 | 946.4959 | 32.1253 | 117.3539 | 118.9055 | 1,510.3084 | 178.9231 |
| within_road | B2_road_scaled | borough | <missing> | 2922 | 51 | 41 | 368.0883 | 673.1362 | 0.3536 | 0.4856 | 0.7656 | 929.4069 | 70.4552 | 184.0442 | 124.4566 | 1,949.1177 | 252.6373 |
| within_road | B2_road_scaled | borough | Bronx | 61773 | 917 | 647 | 116.5761 | 305.2320 | 0.2777 | 0.4197 | 0.8550 | 360.2639 | 24.0065 | 58.2880 | 32.5034 | 565.0994 | 76.5320 |
| within_road | B2_road_scaled | borough | Brooklyn | 86414 | 1317 | 981 | 86.3389 | 214.6141 | 0.2706 | 0.4015 | 0.8697 | 310.7253 | 19.3480 | 43.1695 | 23.0664 | 424.1438 | 57.0559 |
| within_road | B2_road_scaled | borough | Manhattan | 55644 | 756 | 619 | 172.2530 | 335.5719 | 0.3031 | 0.4930 | 0.8441 | 580.1273 | 38.7194 | 86.1265 | 40.7252 | 794.4458 | 110.3807 |
| within_road | B2_road_scaled | borough | Queens | 83711 | 1287 | 931 | 113.3785 | 301.7394 | 0.2854 | 0.4241 | 0.8556 | 361.8893 | 23.8773 | 56.6893 | 30.7600 | 546.3728 | 74.2177 |
| within_road | B2_road_scaled | borough | Staten Island | 22024 | 343 | 234 | 92.9921 | 242.7800 | 0.2981 | 0.4797 | 0.8485 | 291.4627 | 20.7324 | 46.4960 | 24.8808 | 456.1320 | 61.4062 |
| within_road | B2_road_scaled | source | automated | 97233 | 1996 | 1738 | 108.1088 | 271.4555 | 0.3150 | 0.4904 | 0.8296 | 350.1573 | 23.5369 | 54.0544 | 30.7601 | 542.9703 | 72.2343 |
| within_road | B2_road_scaled | source | historical | 215255 | 2883 | 1891 | 123.4051 | 299.6784 | 0.2636 | 0.3934 | 0.8737 | 407.4937 | 26.6118 | 61.7025 | 31.6050 | 582.1682 | 79.9462 |
| within_road | B2_road_scaled | road_type | unspecified | 312488 | 4671 | 3453 | 117.1952 | 289.5928 | 0.2843 | 0.4340 | 0.8561 | 383.4910 | 25.3054 | 58.5976 | 31.1372 | 564.4262 | 76.6935 |
| within_road | B2_road_scaled | training_days_bucket | 15-30 | 103218 | 774 | 522 | 75.2392 | 154.3995 | 0.2478 | 0.3877 | 0.8951 | 341.8380 | 18.7683 | 37.6196 | 22.7709 | 415.3922 | 52.7725 |
| within_road | B2_road_scaled | training_days_bucket | 3-7 | 102943 | 2573 | 2066 | 145.4548 | 350.6121 | 0.3110 | 0.4651 | 0.8258 | 414.8110 | 29.4658 | 72.7274 | 36.5622 | 660.2796 | 92.5036 |
| within_road | B2_road_scaled | training_days_bucket | 31+ | 10661 | 44 | 37 | 103.5864 | 209.2140 | 0.2397 | 0.3799 | 0.8972 | 473.3607 | 26.8692 | 51.7932 | 32.5191 | 593.8827 | 74.1210 |
| within_road | B2_road_scaled | training_days_bucket | 8-14 | 95666 | 1280 | 979 | 86.2271 | 207.3934 | 0.2541 | 0.3959 | 0.8922 | 342.6306 | 20.8417 | 43.1136 | 25.2435 | 460.8523 | 59.4659 |
| within_road | B2_road_scaled | covid | False | 256151 | 4176 | 3099 | 117.7014 | 285.2593 | 0.2821 | 0.4365 | 0.8581 | 388.5563 | 25.8413 | 58.8507 | 30.4576 | 562.9891 | 76.7664 |
| within_road | B2_road_scaled | covid | True | 56337 | 1111 | 796 | 89.8495 | 239.9472 | 0.2796 | 0.3972 | 0.8650 | 324.7678 | 18.1765 | 44.9247 | 28.3945 | 465.7105 | 60.9972 |
| unseen_roads | B1_global | borough | <missing> | 2674 | 10 | 8 | 806.3534 | 1,505.6554 | 0.9983 | 1.4079 | 0.6902 | 871.6080 | 91.9832 | 403.1767 | 538.1530 | 6,301.3617 | 688.8753 |
| unseen_roads | B1_global | borough | Bronx | 54644 | 219 | 159 | 336.5264 | 775.3110 | 0.9124 | 1.2018 | 0.7730 | 868.7534 | 42.6775 | 168.2632 | 176.2122 | 2,188.8963 | 258.1019 |
| unseen_roads | B1_global | borough | Brooklyn | 79800 | 343 | 261 | 279.8074 | 614.1238 | 0.8672 | 1.1718 | 0.8172 | 873.6780 | 36.9894 | 139.9037 | 134.5599 | 1,715.4930 | 207.6353 |
| unseen_roads | B1_global | borough | Manhattan | 46798 | 189 | 158 | 573.4205 | 996.1773 | 1.2029 | 1.5311 | 0.6554 | 871.0039 | 71.0708 | 286.7102 | 260.4214 | 3,314.9224 | 412.1350 |
| unseen_roads | B1_global | borough | Queens | 76857 | 326 | 243 | 336.7622 | 790.8199 | 0.8757 | 1.1920 | 0.8048 | 872.5309 | 44.3618 | 168.3811 | 176.4165 | 2,207.7835 | 259.4396 |
| unseen_roads | B1_global | borough | Staten Island | 21091 | 88 | 63 | 209.8770 | 351.7624 | 0.9121 | 1.2996 | 0.7937 | 882.4597 | 30.8096 | 104.9385 | 79.1090 | 1,099.1861 | 143.2381 |
| unseen_roads | B1_global | source | automated | 95069 | 668 | 571 | 337.4871 | 711.6180 | 1.0094 | 1.3657 | 0.7528 | 875.4940 | 43.8142 | 168.7435 | 160.2931 | 2,041.0731 | 248.5672 |
| unseen_roads | B1_global | source | historical | 186795 | 583 | 382 | 379.0086 | 832.9031 | 0.8626 | 1.1467 | 0.8002 | 871.5775 | 48.8687 | 189.5043 | 193.3766 | 2,422.4530 | 287.8331 |
| unseen_roads | B1_global | road_type | unspecified | 281864 | 1175 | 892 | 352.6527 | 764.0875 | 0.9364 | 1.2588 | 0.7766 | 872.6518 | 45.5823 | 176.3263 | 173.4630 | 2,190.4529 | 263.5811 |
| unseen_roads | B1_global | training_days_bucket | 0 (unseen) | 281864 | 1175 | 892 | 352.6527 | 764.0875 | 0.9364 | 1.2588 | 0.7766 | 872.6518 | 45.5823 | 176.3263 | 173.4630 | 2,190.4529 | 263.5811 |
| unseen_roads | B1_global | covid | False | 232335 | 1063 | 813 | 348.8761 | 741.0075 | 0.9364 | 1.2573 | 0.7772 | 874.2056 | 45.4260 | 174.4380 | 166.3198 | 2,117.4577 | 257.4559 |
| unseen_roads | B1_global | covid | True | 49529 | 254 | 191 | 313.7738 | 753.0064 | 0.8512 | 1.1369 | 0.8128 | 870.5205 | 40.4780 | 156.8869 | 170.1478 | 2,106.2582 | 245.0085 |
| unseen_roads | B2_road_scaled | borough | <missing> | 2674 | 10 | 8 | 975.6261 | 1,302.9285 | 1.3253 | 1.3897 | 0.4380 | 2,098.5874 | 99.2906 | 487.8131 | 282.4359 | 3,817.2655 | 579.6931 |
| unseen_roads | B2_road_scaled | borough | Bronx | 54644 | 219 | 159 | 306.6638 | 691.1679 | 0.8255 | 1.0842 | 0.7785 | 795.3780 | 41.6184 | 153.3319 | 126.3270 | 1,679.4536 | 214.1848 |
| unseen_roads | B2_road_scaled | borough | Brooklyn | 79800 | 343 | 261 | 238.9895 | 545.5464 | 0.7351 | 1.0212 | 0.8344 | 744.9257 | 35.3580 | 119.4947 | 104.1611 | 1,395.1909 | 172.6759 |
| unseen_roads | B2_road_scaled | borough | Manhattan | 46798 | 189 | 158 | 495.8210 | 876.3666 | 0.9613 | 1.3128 | 0.7089 | 1,111.8865 | 68.4488 | 247.9105 | 194.9732 | 2,634.2199 | 340.8883 |
| unseen_roads | B2_road_scaled | borough | Queens | 76857 | 326 | 243 | 305.1047 | 713.9515 | 0.7670 | 1.0500 | 0.8036 | 790.1233 | 42.4920 | 152.5524 | 139.4596 | 1,819.5165 | 223.0027 |
| unseen_roads | B2_road_scaled | borough | Staten Island | 21091 | 88 | 63 | 194.7401 | 334.3557 | 0.8326 | 1.2186 | 0.7919 | 711.6134 | 30.3251 | 97.3700 | 72.6826 | 1,030.0774 | 133.5852 |
| unseen_roads | B2_road_scaled | source | automated | 95069 | 668 | 571 | 302.3292 | 645.3033 | 0.8872 | 1.2440 | 0.7608 | 817.5049 | 42.5652 | 151.1646 | 131.9955 | 1,745.6079 | 217.1503 |
| unseen_roads | B2_road_scaled | source | historical | 186795 | 583 | 382 | 338.3420 | 734.4333 | 0.7352 | 0.9616 | 0.8135 | 850.3185 | 46.9162 | 169.1710 | 135.3600 | 1,822.7619 | 234.2981 |
| unseen_roads | B2_road_scaled | road_type | unspecified | 281864 | 1175 | 892 | 314.2132 | 680.9527 | 0.8095 | 1.1110 | 0.7887 | 834.9208 | 43.9940 | 157.1066 | 131.8528 | 1,758.4683 | 221.9689 |
| unseen_roads | B2_road_scaled | training_days_bucket | 0 (unseen) | 281864 | 1175 | 892 | 314.2132 | 680.9527 | 0.8095 | 1.1110 | 0.7887 | 834.9208 | 43.9940 | 157.1066 | 131.8528 | 1,758.4683 | 221.9689 |
| unseen_roads | B2_road_scaled | covid | False | 232335 | 1063 | 813 | 311.0487 | 659.8592 | 0.8127 | 1.1151 | 0.7894 | 837.8409 | 43.9711 | 155.5243 | 125.9935 | 1,699.6457 | 216.9926 |
| unseen_roads | B2_road_scaled | covid | True | 49529 | 254 | 191 | 276.4267 | 675.9267 | 0.7144 | 0.9721 | 0.8277 | 826.4079 | 38.2759 | 138.2134 | 135.1098 | 1,733.8575 | 207.7327 |
| time | B1_global | borough | <missing> | 1943 | 9 | 5 | 96.3981 | 128.0956 | 0.5012 | 0.7830 | 0.9531 | 690.3192 | 14.7643 | 48.1991 | 57.8437 | 726.0798 | 80.5380 |
| time | B1_global | borough | Bronx | 46053 | 211 | 131 | 167.7825 | 334.1679 | 0.6968 | 0.9458 | 0.8148 | 691.3672 | 25.5177 | 83.8912 | 74.6774 | 1,001.9505 | 122.7242 |
| time | B1_global | borough | Brooklyn | 65958 | 303 | 192 | 150.0748 | 226.2099 | 0.6300 | 0.8078 | 0.8649 | 690.9155 | 21.6683 | 75.0374 | 57.3049 | 789.7322 | 102.6738 |
| time | B1_global | borough | Manhattan | 56963 | 249 | 200 | 306.1037 | 496.6289 | 0.8033 | 0.9964 | 0.7342 | 691.6498 | 42.1280 | 153.0518 | 105.1103 | 1,472.3831 | 200.1934 |
| time | B1_global | borough | Queens | 66078 | 287 | 191 | 180.8724 | 402.1569 | 0.7032 | 0.9423 | 0.8336 | 691.9525 | 25.5683 | 90.4362 | 78.9275 | 1,044.9576 | 129.9546 |
| time | B1_global | borough | Staten Island | 21872 | 95 | 56 | 171.8441 | 300.7212 | 0.7292 | 0.9887 | 0.7793 | 691.7684 | 26.9140 | 85.9220 | 68.9713 | 958.8532 | 121.2049 |
| time | B1_global | source | automated | 33736 | 158 | 122 | 295.9972 | 631.3866 | 0.7711 | 0.9976 | 0.7935 | 702.4080 | 40.7343 | 147.9986 | 127.6897 | 1,684.2402 | 210.9484 |
| time | B1_global | source | historical | 225131 | 1064 | 706 | 179.9475 | 304.4394 | 0.7009 | 0.9257 | 0.8164 | 688.3421 | 26.2827 | 89.9737 | 68.9460 | 952.2872 | 123.4683 |
| time | B1_global | road_type | unspecified | 258867 | 1154 | 775 | 196.0119 | 367.2413 | 0.7050 | 0.9247 | 0.8134 | 691.4800 | 28.1347 | 98.0059 | 77.1385 | 1,052.7317 | 135.5194 |
| time | B1_global | training_days_bucket | 15-30 | 123245 | 545 | 360 | 180.2213 | 340.0562 | 0.6697 | 0.8811 | 0.8334 | 690.9567 | 26.1918 | 90.1106 | 72.5998 | 987.9156 | 125.9348 |
| time | B1_global | training_days_bucket | 3-7 | 4866 | 24 | 19 | 216.5868 | 306.5485 | 0.7206 | 0.8958 | 0.8409 | 695.5950 | 33.5007 | 108.2934 | 52.2069 | 857.0764 | 129.3340 |
| time | B1_global | training_days_bucket | 31+ | 6580 | 17 | 15 | 211.1992 | 312.0571 | 0.6311 | 0.7911 | 0.8243 | 703.9723 | 33.5458 | 105.5996 | 54.1721 | 877.1786 | 128.8783 |
| time | B1_global | training_days_bucket | 8-14 | 124176 | 568 | 396 | 209.8392 | 395.1777 | 0.7404 | 0.9695 | 0.7927 | 691.4343 | 29.6102 | 104.9196 | 83.2343 | 1,128.4447 | 145.1761 |
| time | B1_global | covid | False | 113853 | 534 | 356 | 224.1288 | 413.4943 | 0.6997 | 0.9105 | 0.8102 | 691.8079 | 31.7143 | 112.0644 | 85.0431 | 1,167.5747 | 152.5479 |
| time | B1_global | covid | True | 145014 | 657 | 446 | 180.3079 | 363.4561 | 0.7112 | 0.9409 | 0.8164 | 692.2089 | 26.0701 | 90.1539 | 75.3227 | 1,013.9276 | 127.6978 |
| time | B2_road_scaled | borough | <missing> | 1943 | 9 | 5 | 86.7793 | 139.2484 | 0.4443 | 0.7280 | 0.6278 | 180.9893 | 27.9752 | 43.3896 | 17.2587 | 452.3390 | 59.0824 |
| time | B2_road_scaled | borough | Bronx | 46053 | 211 | 131 | 76.2840 | 259.9891 | 0.2875 | 0.4542 | 0.7622 | 177.6165 | 13.8722 | 38.1420 | 32.9927 | 468.6497 | 56.6713 |
| time | B2_road_scaled | borough | Brooklyn | 65958 | 303 | 192 | 77.6479 | 129.2997 | 0.3225 | 0.4404 | 0.6698 | 186.8491 | 15.3972 | 38.8239 | 24.1546 | 395.5188 | 52.2505 |
| time | B2_road_scaled | borough | Manhattan | 56963 | 249 | 200 | 179.1085 | 282.3774 | 0.4419 | 0.6295 | 0.5751 | 395.9382 | 46.7090 | 89.5543 | 42.8687 | 895.7762 | 119.4213 |
| time | B2_road_scaled | borough | Queens | 66078 | 287 | 191 | 88.4243 | 289.7015 | 0.3303 | 0.4777 | 0.6781 | 202.4688 | 16.4194 | 44.2121 | 33.7916 | 502.1102 | 62.9488 |
| time | B2_road_scaled | borough | Staten Island | 21872 | 95 | 56 | 88.4573 | 153.5610 | 0.3747 | 0.5023 | 0.6128 | 206.4608 | 17.4553 | 44.2286 | 29.2529 | 467.0824 | 60.6246 |
| time | B2_road_scaled | source | automated | 33736 | 158 | 122 | 156.0561 | 426.5823 | 0.3874 | 0.5925 | 0.6340 | 280.8050 | 33.6212 | 78.0281 | 61.0274 | 946.4865 | 115.1178 |
| time | B2_road_scaled | source | historical | 225131 | 1064 | 706 | 95.4831 | 192.3873 | 0.3515 | 0.5129 | 0.6644 | 228.5060 | 21.2122 | 47.7416 | 28.0662 | 492.7847 | 64.6800 |
| time | B2_road_scaled | road_type | unspecified | 258867 | 1154 | 775 | 102.9319 | 238.5767 | 0.3491 | 0.5058 | 0.6633 | 235.7298 | 22.3963 | 51.4660 | 32.5712 | 549.6751 | 70.9557 |
| time | B2_road_scaled | training_days_bucket | 15-30 | 123245 | 545 | 360 | 100.0156 | 245.7746 | 0.3405 | 0.4954 | 0.6770 | 234.6989 | 21.7324 | 50.0078 | 30.5916 | 523.2409 | 68.2212 |
| time | B2_road_scaled | training_days_bucket | 3-7 | 4866 | 24 | 19 | 96.5974 | 161.8413 | 0.3122 | 0.4312 | 0.6697 | 230.0497 | 19.6303 | 48.2987 | 31.2766 | 509.0688 | 66.1371 |
| time | B2_road_scaled | training_days_bucket | 31+ | 6580 | 17 | 15 | 93.9454 | 154.0273 | 0.3058 | 0.4962 | 0.7508 | 282.3624 | 26.3805 | 46.9727 | 26.3436 | 527.2415 | 66.4646 |
| time | B2_road_scaled | training_days_bucket | 8-14 | 124176 | 568 | 396 | 106.2669 | 236.3588 | 0.3601 | 0.5187 | 0.6474 | 235.5633 | 23.0309 | 53.1334 | 34.7117 | 577.4261 | 73.9174 |
| time | B2_road_scaled | covid | False | 113853 | 534 | 356 | 116.1418 | 294.2880 | 0.3283 | 0.4907 | 0.6883 | 246.0106 | 23.8594 | 58.0709 | 43.4593 | 673.1865 | 83.5930 |
| time | B2_road_scaled | covid | True | 145014 | 657 | 446 | 97.9532 | 239.0820 | 0.3697 | 0.5317 | 0.6435 | 228.4860 | 21.9641 | 48.9766 | 28.6202 | 505.8428 | 66.3739 |
| season_transfer | B1_global | borough | Brooklyn | 768 | 7 | 6 | 199.9534 | 279.9799 | 0.7428 | 0.8866 | 0.7988 | 767.5135 | 29.7148 | 99.9767 | 57.5624 | 872.7714 | 124.8359 |
| season_transfer | B1_global | borough | Manhattan | 96 | 1 | 1 | 636.4583 | 679.2371 | 1.5696 | 1.6847 | 0.2292 | 611.2917 | 77.6604 | 318.2292 | 182.8333 | 2,604.9375 | 385.8153 |
| season_transfer | B1_global | borough | Queens | 1296 | 4 | 4 | 521.4956 | 956.3013 | 1.0710 | 1.3594 | 0.5816 | 749.9027 | 60.3644 | 260.7478 | 311.8814 | 3,722.4582 | 421.9957 |
| season_transfer | B1_global | borough | Staten Island | 1464 | 5 | 4 | 92.3869 | 122.1420 | 0.4622 | 0.6143 | 0.9654 | 789.8393 | 16.3133 | 46.1934 | 62.8689 | 791.8224 | 83.5838 |
| season_transfer | B1_global | source | automated | 3624 | 17 | 15 | 269.6499 | 528.1893 | 0.7861 | 1.0180 | 0.7632 | 760.7467 | 35.8052 | 134.8250 | 126.3318 | 1,621.3695 | 197.9746 |
| season_transfer | B1_global | road_type | unspecified | 3624 | 17 | 15 | 269.6499 | 528.1893 | 0.7861 | 1.0180 | 0.7632 | 760.7467 | 35.8052 | 134.8250 | 126.3318 | 1,621.3695 | 197.9746 |
| season_transfer | B1_global | training_days_bucket | 3-7 | 3336 | 13 | 11 | 313.9311 | 596.4601 | 0.8121 | 1.0565 | 0.7640 | 763.3909 | 40.3798 | 156.9655 | 145.8250 | 1,862.0473 | 228.7802 |
| season_transfer | B1_global | training_days_bucket | 8-14 | 288 | 4 | 4 | 125.7361 | 171.6000 | 0.7018 | 0.8814 | 0.7604 | 752.1528 | 20.9378 | 62.8681 | 62.9788 | 839.1667 | 97.8565 |
| season_transfer | B1_global | covid | False | 2736 | 12 | 11 | 156.0473 | 244.9688 | 0.6983 | 0.9148 | 0.7844 | 757.0773 | 22.9771 | 78.0237 | 74.1985 | 971.7563 | 116.7995 |
| season_transfer | B1_global | covid | True | 888 | 5 | 4 | 542.2962 | 896.9515 | 0.9969 | 1.2310 | 0.7124 | 769.5531 | 66.5926 | 271.1481 | 251.4515 | 3,180.4412 | 392.7948 |
| season_transfer | B2_road_scaled | borough | Brooklyn | 768 | 7 | 6 | 70.5015 | 105.3686 | 0.3025 | 0.4074 | 0.9478 | 561.9283 | 18.2589 | 35.2507 | 38.9438 | 572.0271 | 61.6356 |
| season_transfer | B2_road_scaled | borough | Manhattan | 96 | 1 | 1 | 395.6166 | 431.5058 | 0.7507 | 0.8594 | 0.6667 | 814.8291 | 64.1786 | 197.8083 | 76.3440 | 1,405.2262 | 225.5540 |
| season_transfer | B2_road_scaled | borough | Queens | 1296 | 4 | 4 | 232.5376 | 433.8165 | 0.4193 | 0.5054 | 0.8898 | 859.8562 | 40.9644 | 116.2688 | 54.2248 | 951.8920 | 140.9720 |
| season_transfer | B2_road_scaled | borough | Staten Island | 1464 | 5 | 4 | 59.1229 | 78.7507 | 0.3289 | 0.4788 | 0.9151 | 484.3964 | 12.0061 | 29.5615 | 37.6880 | 496.9417 | 52.8371 |
| season_transfer | B2_road_scaled | source | automated | 3624 | 17 | 15 | 124.4054 | 248.2540 | 0.3641 | 0.4892 | 0.9080 | 624.1020 | 24.4635 | 62.2027 | 44.3700 | 688.3348 | 87.3575 |
| season_transfer | B2_road_scaled | road_type | unspecified | 3624 | 17 | 15 | 124.4054 | 248.2540 | 0.3641 | 0.4892 | 0.9080 | 624.1020 | 24.4635 | 62.2027 | 44.3700 | 688.3348 | 87.3575 |
| season_transfer | B2_road_scaled | training_days_bucket | 3-7 | 3336 | 13 | 11 | 144.8716 | 279.9236 | 0.3678 | 0.4972 | 0.9011 | 705.1672 | 28.5500 | 72.4358 | 49.9358 | 784.8582 | 100.6144 |
| season_transfer | B2_road_scaled | training_days_bucket | 8-14 | 288 | 4 | 4 | 57.8904 | 85.2441 | 0.3518 | 0.4623 | 0.9306 | 360.6401 | 11.1823 | 28.9452 | 26.2811 | 374.6341 | 44.2724 |
| season_transfer | B2_road_scaled | covid | False | 2736 | 12 | 11 | 87.0228 | 147.7637 | 0.3844 | 0.5182 | 0.8980 | 444.6210 | 14.9744 | 43.5114 | 35.5759 | 505.5035 | 62.7078 |
| season_transfer | B2_road_scaled | covid | True | 888 | 5 | 4 | 214.1237 | 396.4091 | 0.3154 | 0.4114 | 0.9321 | 1,054.8563 | 47.2372 | 107.0618 | 65.4758 | 1,127.1302 | 146.5166 |
