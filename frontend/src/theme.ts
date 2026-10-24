import { BrandVariants, createLightTheme, Theme } from '@fluentui/react-components';

const downitBrand: BrandVariants = {
  10: '#02120a',
  20: '#04200f',
  30: '#062d16',
  40: '#083a1d',
  50: '#0a4824',
  60: '#0c562b',
  70: '#0e6432',
  80: '#107339',
  90: '#128140',
  100: '#158f47',
  110: '#219d53',
  120: '#32ab61',
  130: '#48b871',
  140: '#63c583',
  150: '#82d197',
  160: '#a4deae',
};

export const downitTheme: Theme = {
  ...createLightTheme(downitBrand),
  fontFamilyBase:
    '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif',
};
