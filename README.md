# Insta360 Docker Stitcher

這個專案會建立一個 Docker image，讓你把 Insta360 原始 `.insv` / `.insp` 媒體直接丟進容器，透過 **Insta360 MediaSDK** 輸出成 2:1 的 360 `.mp4` / stitched image。

> `Desktop-CameraSDK-Cpp` 主要是控制相機；離線把 `.insv` 拼接成 `.mp4` 需要的是 **Desktop-MediaSDK-Cpp / MediaSDK**。

## 功能

- 支援單一 `.insv` / `.insp` 或整個資料夾批次處理
- 自動配對常見的 Insta360 雙檔命名：`*_00_*.insv` / `*_10_*.insv`
- 預設使用 `optflow + stitchfusion + FlowState + direction lock + H.265`
- 預設輸出 `7680x3840`、`80 Mbps`
- 會在輸出後用 `ffprobe` 再確認 MP4 可讀
- `local-sdk/` 這套會用 MediaSDK 的 `GetMediaFileInfo` 自動判斷一般 360 影片 / 子彈時間 / 縮時 / 360 照片 等 media type，並套用對應預設

## 需求

1. Linux + Docker
2. NVIDIA GPU 與驅動
3. Docker runtime 可使用 GPU（執行時要加 `--gpus all`）
4. Insta360 **MediaSDK** 的 Linux `.deb`
5. 只有在使用 `--stitch-type aistitch` 時才需要 AI model

## 取得 MediaSDK

請先到 Insta360 開發者頁面申請與下載官方 SDK：

- https://www.insta360.com/sdk/apply
- https://github.com/Insta360Develop/Desktop-MediaSDK-Cpp

本專案現在預設會直接從 EasyGaussianSplatting 使用的 `.deb` 來源下載：

```text
https://github.com/MapMindAI/EasyGaussianSplatting/releases/download/v1/Insta360SDK.deb
```

## 使用你本地下載的新 SDK（推薦）

如果你現在手上已經有官方的 `Linux_CameraSDK-2.1.1_MediaSDK-3.1.1` 套件，請直接用 `local-sdk/` 這套流程。它會：

1. 從 `sdk/` 目錄裡的 `libMediaSDK-dev-*.tar*.xz` 安裝新的 MediaSDK
2. 在 base image 內一起放好完整 `models/` 目錄
3. 額外建立 **base / dev / final** 三層 image
4. 用 SDK 的 `GetMediaFileInfo(...)` 先判斷 media type，再決定預設 stitching profile

### local-sdk 建立 image

```bash
docker compose -f local-sdk/docker-compose.yaml build stitch
docker compose -f local-sdk/docker-compose.yaml build stitch-dev
```

### local-sdk 執行 stitching

```bash
docker compose -f local-sdk/docker-compose.yaml run --rm stitch /data/input /data/output
```

### local-sdk 進 dev image

```bash
docker compose -f local-sdk/docker-compose.yaml run --rm stitch-dev
```

### local-sdk media type 自動判斷

`local-sdk/` 這套目前會自動辨識這類型別：

- 一般 360 影片（`VIDEO_NORMAL`）
- 子彈時間（`VIDEO_BULLETTIME`）
- 縮時 / 移動縮時 / 星空（例如 `VIDEO_TIMELAPSE` / `VIDEO_TIMESHIFT` / `VIDEO_STARLAPSE`）
- 360 照片（例如 `PHOTO_NORMAL` / `PHOTO_HDR` / `PHOTO_PANO_MODE`）

目前 **不能只靠 MediaSDK metadata 自動判斷潛水殼 / 水下拍攝**；這種情況仍然要手動指定：

enum class CameraAccessoryType {
        kNormal = 0,
        kWaterproof = 1,            // (one/onex/onex2/oner/oners/onex2/onex3) 潜水壳
        kOnerLensGuard = 2,         // (oner/oners) 黏贴式保护镜
        kOnerLensGuardPro = 3,      // (oner/oners) 卡扣式保护镜
        kOnex2LensGuard = 4,        // (oner/oners/onex2/onex3) 黏贴式保护镜
        kOnex2LensGuardPro = 5,     // (onex2)卡扣式保护镜
        k283PanoLensGuardPro = 6,   // (oner/oners) 283全景镜头的卡扣式保护镜
        kDiveCaseAir = 7,           // (onex/onex2/oner/oners/onex2/onex3) 潜水壳(水上)
        kDiveCaseWater = 8,         // (onex/onex2/oner/oners/onex2/onex3) 潜水壳(水下)
        kInvisibleDiveCaseAir = 9,  // X3/X4/X5 全隐形潜水壳(水上)
        kInvisibleDiveCaseWater = 10,// X3/X4/X5 全隐形潜水壳(水下)
        kLensGuardA = 11,            // X3/X4/x5 A级塑胶保护镜
        kLensGuardS = 12,            // X3/X4/x5 S级玻璃保护镜
        kLensGuardAS = 13,           // X4 自动识别
    };

```bash
docker compose -f local-sdk/docker-compose.yaml run --rm stitch \
  /data/input /data/output \
  --camera-accessory-type 10
```

`CameraAccessoryType` 的 enum 可參考 SDK header；例如新 SDK 裡有：

- `7`: `kDiveCaseWater`
- `9`: `kInvisibleDiveCaseWater`
- `10`: `kLensGuardA`
- `11`: `kLensGuardS`
- `12`: `kLensGuardAS`

另外，`VIDEO_BULLETTIME` 目前在這套 MediaSDK offline stitching 流程裡沒有找到官方 SDK / GitHub 提供的可用 export path；實際送進 `VideoStitcher` 也會回 `ErrorCode 11`。所以 `local-sdk/` 會直接略過 bullet time 素材，並把原始檔移到輸出目錄下的 `bullet_time/` 子資料夾。

現在建議先建一層 **base image**，把系統套件和 MediaSDK `.deb` 下載 / 安裝好，之後主 image 直接站在這層上面，就不用每次重新下載：

```bash
docker build -f Dockerfile.base -t insta360-stitch-base .
docker build -t insta360-stitch .
```

如果你想直接用 Docker Compose，也可以：

```bash
docker compose build stitch
```

這會先建立 `stitch-base`，再用它建 `stitch`。

如果你想改成使用自己下載好的 `.deb`，也可以把檔案放到專案的 `sdk/` 目錄，例如：

```bash
mkdir -p sdk
cp ~/Downloads/Insta360_MediaSDK_Linux.deb sdk/
```

如果你想顯式指定別的來源，也可以在建立 base image 時覆蓋：

```bash
docker build -f Dockerfile.base \
  --build-arg MEDIA_SDK_DEB_URL=https://github.com/MapMindAI/EasyGaussianSplatting/releases/download/v1/Insta360SDK.deb \
  -t insta360-stitch-base .
```

Compose 版則可以直接覆蓋環境變數：

```bash
MEDIA_SDK_DEB_URL=https://github.com/MapMindAI/EasyGaussianSplatting/releases/download/v1/Insta360SDK.deb \
docker compose build stitch
```

如果 `sdk/` 裡有 `.deb`，安裝腳本會優先使用本機檔案；沒有時才會下載預設 URL。主 image 預設會使用 `insta360-stitch-base:latest`，也可以改成別的 base image：

```bash
docker build \
  --build-arg BASE_IMAGE=insta360-stitch-base:latest \
  -t insta360-stitch .
```

## 建立 image

```bash
docker build -f Dockerfile.base -t insta360-stitch-base .
docker build -t insta360-stitch .
```

或是：

```bash
docker compose build stitch
```

建置時會：

1. 在 `Dockerfile.base` 安裝 MediaSDK `.deb`
2. 在 `Dockerfile.base` 找出 SDK 的 headers / libs / AI model 目錄
3. 在主 `Dockerfile` 編譯 `insta360_media_stitcher`
4. 在主 `Dockerfile` 建立批次入口 `insta360-stitch-batch`

## 使用方式

### 1. 批次處理整個資料夾

```bash
docker run --rm --gpus all \
  -v "$PWD/data:/data" \
  insta360-stitch \
  /data/input \
  /data/output
```

Compose 寫法：

```bash
docker compose run --rm stitch /data/input /data/output
```

### 2. 處理單一 `.insv`

```bash
docker run --rm --gpus all \
  -v "$PWD/data:/data" \
  insta360-stitch \
  /data/input/VID_20260422_153814_00_004.insv \
  /data/output
```

Compose 寫法：

```bash
docker compose run --rm stitch /data/input/VID_20260422_153814_00_004.insv /data/output
```

### 3. 指定 X5 使用 v2 AI model

只有在你明確使用 `aistitch` 時，才需要這組參數：

通常腳本會先用 `exiftool` 嘗試判斷相機型號；如果你想手動指定：

```bash
docker run --rm --gpus all \
  -v "$PWD/data:/data" \
  insta360-stitch \
  /data/input \
  /data/output \
  --stitch-type aistitch \
  --model-version v2
```

### 4. 如果 SDK 的 model 沒有被自動找到

這也只適用於 `--stitch-type aistitch`：

把 model 目錄另外掛進容器並指定 `--model-root`：

```bash
docker run --rm --gpus all \
  -v "$PWD/data:/data" \
  -v "/path/to/sdk-models:/models:ro" \
  insta360-stitch \
  /data/input \
  /data/output \
  --stitch-type aistitch \
  --model-root /models
```

## 可調參數

```bash
docker run --rm --gpus all \
  -v "$PWD/data:/data" \
  insta360-stitch \
  /data/input \
  /data/output \
  --output-size 8000x4000 \
  --bitrate 150000000 \
  --camera-accessory-type 0
```

也支援：

- `--disable-flowstate`
- `--disable-directionlock`
- `--disable-stitchfusion`
- `--disable-h265`
- `--disable-gpu`
- `--enable-soft-encode`
- `--enable-soft-decode`
- `--overwrite`
- `--recursive`
- `--dry-run`

## 直接進容器

```bash
docker run --rm -it --gpus all \
  -v "$PWD/data:/data" \
  --entrypoint bash \
  insta360-stitch
```

容器內可以直接執行：

```bash
insta360-stitch-batch /data/input /data/output
insta360_media_stitcher -help
```
