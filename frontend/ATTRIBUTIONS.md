This Figma Make file includes components from [shadcn/ui](https://ui.shadcn.com/) used under [MIT license](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md).

This Figma Make file includes photos from [Unsplash](https://unsplash.com) used under [license](https://unsplash.com/license).

## 글꼴

`public/fonts/` 의 woff2 는 아래 원본을 서브셋한 것이다. 둘 다 SIL Open Font
License 1.1 이라 고지 의무가 있다. 서브셋 방법과 범위는
[`scripts/build-fonts.sh`](scripts/build-fonts.sh) 에 있다.

| 파일 | 원본 | 저작자 | 라이선스 |
| --- | --- | --- | --- |
| `AstaSans-latin.woff2`, `AstaSans-korean.woff2` | [Asta Sans](https://github.com/42dot/Asta-Sans) | 42dot Inc. | [OFL 1.1](https://github.com/42dot/Asta-Sans/blob/main/OFL.txt) |
| `D2Coding-mono.woff2`, `D2Coding-mono-bold.woff2` | [D2Coding](https://github.com/Joungkyun/font-d2coding) (웹폰트 변환: JoungKyun Kim) / [원본](https://github.com/naver/d2codingfont) | NAVER Corp. | [OFL 1.1](https://github.com/Joungkyun/font-d2coding/blob/master/License) |

OFL 1.1 은 서브셋과 재배포를 허용하되 원본 글꼴 이름(Reserved Font Name)을
쓰지 말 것을 요구한다. 파일 이름은 바꿨지만 CSS 의 `font-family` 는 원래
이름을 그대로 쓴다 — OFL 이 제한하는 것은 파생 글꼴의 **이름 붙이기**이고,
여기서는 글리프를 고치지 않고 문자 범위만 줄였다.
