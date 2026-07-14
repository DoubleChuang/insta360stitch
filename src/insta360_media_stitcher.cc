#include <ins_stitcher.h>

#include <condition_variable>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Options {
    std::vector<std::string> inputs;
    std::string output_path;
    std::string model_root_dir;
    std::string ai_stitching_model;
    ins::STITCH_TYPE stitch_type = ins::STITCH_TYPE::AIFLOW;
    int output_width = 7680;
    int output_height = 3840;
    int output_bitrate = 80000000;
    bool enable_flowstate = true;
    bool enable_directionlock = true;
    bool enable_stitchfusion = true;
    bool enable_h265_encoder = true;
    bool enable_cuda = true;
    bool enable_soft_encode = false;
    bool enable_soft_decode = false;
    ins::CameraAccessoryType accessory_type = ins::CameraAccessoryType::kNormal;
    bool show_help = false;
};

const char kHelpText[] =
    "insta360_media_stitcher\n"
    "  -inputs <file1> [file2]\n"
    "  -output <output.mp4>\n"
    "  [-model_root_dir <dir>]\n"
    "  [-ai_stitching_model <model.ins>]\n"
    "  [-stitch_type template|optflow|dynamicstitch|aistitch]\n"
    "  [-output_size WIDTHxHEIGHT]\n"
    "  [-bitrate BPS]\n"
    "  [-camera_accessory_type N]\n"
    "  [-enable_flowstate]\n"
    "  [-enable_directionlock]\n"
    "  [-enable_stitchfusion]\n"
    "  [-enable_h265_encoder]\n"
    "  [-disable_cuda]\n"
    "  [-enable_soft_encode]\n"
    "  [-enable_soft_decode]\n"
    "  [-help]\n";

bool FileExists(const std::string& path) {
    std::ifstream stream(path);
    return stream.good();
}

bool EndsWithSlash(const std::string& path) {
    return !path.empty() && (path.back() == '/' || path.back() == '\\');
}

std::string DirName(const std::string& path) {
    const std::size_t separator = path.find_last_of("/\\");
    if (separator == std::string::npos) {
        return ".";
    }
    if (separator == 0) {
        return path.substr(0, 1);
    }
    return path.substr(0, separator);
}

std::string JoinPath(const std::string& base, const std::string& name) {
    if (base.empty()) {
        return name;
    }
    return EndsWithSlash(base) ? base + name : base + "/" + name;
}

bool ParseOutputSize(const std::string& text, int& width, int& height) {
    const std::size_t separator = text.find('x');
    if (separator == std::string::npos) {
        return false;
    }

    width = std::atoi(text.substr(0, separator).c_str());
    height = std::atoi(text.substr(separator + 1).c_str());
    return width > 0 && height > 0;
}

bool ParseStitchType(const std::string& text, ins::STITCH_TYPE& stitch_type) {
    if (text == "template") {
        stitch_type = ins::STITCH_TYPE::TEMPLATE;
        return true;
    }
    if (text == "optflow") {
        stitch_type = ins::STITCH_TYPE::OPTFLOW;
        return true;
    }
    if (text == "dynamicstitch") {
        stitch_type = ins::STITCH_TYPE::DYNAMICSTITCH;
        return true;
    }
    if (text == "aistitch") {
        stitch_type = ins::STITCH_TYPE::AIFLOW;
        return true;
    }
    return false;
}

bool RequireValue(int argc, char* argv[], int& index, std::string& value, std::string& error) {
    if (index + 1 >= argc) {
        error = "Missing value for " + std::string(argv[index]);
        return false;
    }

    value = argv[++index];
    return true;
}

bool ParseArguments(int argc, char* argv[], Options& options, std::string& error) {
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];

        if (argument == "-help" || argument == "--help") {
            options.show_help = true;
            return true;
        }

        if (argument == "-inputs") {
            while (index + 1 < argc && argv[index + 1][0] != '-') {
                options.inputs.emplace_back(argv[++index]);
            }
            if (options.inputs.empty()) {
                error = "No input files were provided after -inputs";
                return false;
            }
            continue;
        }

        std::string value;
        if (argument == "-output") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            options.output_path = value;
        } else if (argument == "-model_root_dir") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            options.model_root_dir = value;
        } else if (argument == "-ai_stitching_model") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            options.ai_stitching_model = value;
        } else if (argument == "-stitch_type") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            if (!ParseStitchType(value, options.stitch_type)) {
                error = "Unsupported stitch type: " + value;
                return false;
            }
        } else if (argument == "-output_size") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            if (!ParseOutputSize(value, options.output_width, options.output_height)) {
                error = "Invalid output size: " + value;
                return false;
            }
        } else if (argument == "-bitrate") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            options.output_bitrate = std::atoi(value.c_str());
        } else if (argument == "-camera_accessory_type") {
            if (!RequireValue(argc, argv, index, value, error)) {
                return false;
            }
            options.accessory_type = static_cast<ins::CameraAccessoryType>(std::atoi(value.c_str()));
        } else if (argument == "-enable_flowstate") {
            options.enable_flowstate = true;
        } else if (argument == "-enable_directionlock") {
            options.enable_directionlock = true;
        } else if (argument == "-enable_stitchfusion") {
            options.enable_stitchfusion = true;
        } else if (argument == "-enable_h265_encoder") {
            options.enable_h265_encoder = true;
        } else if (argument == "-disable_cuda") {
            options.enable_cuda = false;
        } else if (argument == "-enable_soft_encode") {
            options.enable_soft_encode = true;
        } else if (argument == "-enable_soft_decode") {
            options.enable_soft_decode = true;
        } else {
            error = "Unknown argument: " + argument;
            return false;
        }
    }

    if (options.inputs.empty()) {
        error = "At least one input file is required.";
        return false;
    }

    if (options.output_path.empty()) {
        error = "An output file is required.";
        return false;
    }

    if (options.output_width != options.output_height * 2) {
        error = "Output size must keep a 2:1 aspect ratio.";
        return false;
    }

    return true;
}

bool ResolveAiModel(Options& options, std::string& error) {
    if (options.stitch_type != ins::STITCH_TYPE::AIFLOW) {
        return true;
    }

    if (!options.ai_stitching_model.empty()) {
        if (!FileExists(options.ai_stitching_model)) {
            error = "AI stitch model does not exist: " + options.ai_stitching_model;
            return false;
        }
        if (options.model_root_dir.empty()) {
            options.model_root_dir = DirName(options.ai_stitching_model);
        }
    }

    if (options.model_root_dir.empty()) {
        error = "AI stitch mode requires -ai_stitching_model or -model_root_dir.";
        return false;
    }

    const std::string v1 = JoinPath(options.model_root_dir, "ai_stitch_model_v1.ins");
    const std::string v2 = JoinPath(options.model_root_dir, "ai_stitch_model_v2.ins");
    if (FileExists(v1)) {
        return true;
    }
    if (FileExists(v2)) {
        return true;
    }

    error = "Could not find ai_stitch_model_v1.ins or ai_stitch_model_v2.ins under " +
            options.model_root_dir;
    return false;
}

}  // namespace

int main(int argc, char* argv[]) {
    ins::SetLogLevel(ins::InsLogLevel::ERR);
    ins::InitEnv();

    Options options;
    std::string error;

    if (!ParseArguments(argc, argv, options, error)) {
        std::cerr << error << "\n\n" << kHelpText;
        return 1;
    }

    if (options.show_help) {
        std::cout << kHelpText;
        return 0;
    }

    if (!ResolveAiModel(options, error)) {
        std::cerr << error << "\n";
        return 1;
    }

    if (options.stitch_type == ins::STITCH_TYPE::AIFLOW) {
        ins::SetModelFileRootDir(options.model_root_dir);
    }

    auto stitcher = std::make_shared<ins::VideoStitcher>();
    stitcher->SetInputPath(options.inputs);
    stitcher->SetOutputPath(options.output_path);
    stitcher->SetStitchType(options.stitch_type);
    stitcher->SetOutputSize(options.output_width, options.output_height);
    if (options.output_bitrate > 0) {
        stitcher->SetOutputBitRate(options.output_bitrate);
    }
    stitcher->EnableFlowState(options.enable_flowstate);
    stitcher->EnableDirectionLock(options.enable_directionlock);
    stitcher->EnableStitchFusion(options.enable_stitchfusion);
    stitcher->EnableCuda(options.enable_cuda);
    stitcher->SetCameraAccessoryType(options.accessory_type);
    stitcher->SetSoftwareCodecUsage(options.enable_soft_encode, options.enable_soft_decode);
    if (options.enable_h265_encoder) {
        stitcher->EnableH265Encoder();
    }

    std::mutex mutex;
    std::condition_variable condition;
    bool finished = false;
    bool failed = false;
    int last_progress = -1;

    stitcher->SetStitchProgressCallback([&](int progress, int) {
        if (progress != last_progress) {
            std::cout << "\rprogress=" << progress << "%" << std::flush;
            last_progress = progress;
        }
        if (progress >= 100) {
            std::cout << std::endl;
            {
                std::lock_guard<std::mutex> guard(mutex);
                finished = true;
            }
            condition.notify_one();
        }
    });

    stitcher->SetStitchStateCallback([&](int code, const char* message) {
        std::cerr << "\nerror[" << code << "]: " << (message ? message : "unknown") << std::endl;
        {
            std::lock_guard<std::mutex> guard(mutex);
            finished = true;
            failed = true;
        }
        condition.notify_one();
    });

    std::cout << "starting stitch" << std::endl;
    stitcher->StartStitch();

    {
        std::unique_lock<std::mutex> lock(mutex);
        condition.wait(lock, [&] { return finished; });
    }

    return failed ? 2 : 0;
}
