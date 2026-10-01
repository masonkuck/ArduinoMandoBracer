using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text.Json;

// ============================================================
// GIF -> RGB565 FRAME CONVERTER
// ============================================================
//
// Converts animated GIFs into raw frames for the ST7789 240x320
// panel driven by code.py. All frames go into one frames.bin so
// the Feather can load the whole animation into RAM in one read.
//
// Every output frame is always 240 x 320 in the panel's native
// portrait memory order. Rotation is baked into the pixels here,
// so the Feather never rotates anything; it just copies bytes.
//

const int PanelWidth = 240;
const int PanelHeight = 320;

Options options;

try
{
    options = Options.Parse(args);
}
catch (ArgumentException e)
{
    Console.Error.WriteLine($"ERROR: {e.Message}");
    Console.Error.WriteLine("Run with --help for usage.");
    return 1;
}

if (options.ShowHelp)
{
    Options.PrintHelp();
    return 0;
}

int failures = 0;

foreach (string input in options.Inputs)
{
    try
    {
        Converter.Convert(input, options, PanelWidth, PanelHeight);
    }
    catch (Exception e)
    {
        Console.Error.WriteLine($"ERROR converting {input}: {e.Message}");
        failures++;
    }

    Console.WriteLine();
}

return failures == 0 ? 0 : 1;


// ============================================================
// OPTIONS
// ============================================================

enum ColorFormat
{
    Rgb444,
    Rgb565,
}

enum FitMode
{
    Stretch,
    Contain,
    Cover,
}

sealed class Options
{
    public List<string> Inputs { get; } = new();
    // null = auto: landscape GIFs get 90, portrait GIFs get 0.
    public int? Rotation { get; private set; }
    public FitMode Fit { get; private set; } = FitMode.Contain;
    public bool Smooth { get; private set; } = false;
    public string? OutputDirectory { get; private set; }
    public int DefaultDelayMs { get; private set; } = 100;
    public Color Background { get; private set; } = Color.Black;
    public ColorFormat Format { get; private set; } = ColorFormat.Rgb444;
    public bool Dither { get; private set; } = false;
    public bool ShowHelp { get; private set; }

    public static void PrintHelp()
    {
        Console.WriteLine(
            """
            GifConverter - convert animated GIFs to raw frames for the
            ST7789 240x320 display (FeatherS3 + code.py).

            USAGE
              GifConverter <input.gif> [more.gif ...] [options]

            OPTIONS
              -r, --rotation <deg>    How the GIF is turned on the panel:
                                        auto  pick from the GIF's shape (default):
                                              landscape -> 90, portrait -> 0
                                        0     portrait (GIF scaled to 240x320)
                                        90    landscape, rotated clockwise
                                        180   portrait, upside down
                                        270   landscape, rotated counter-clockwise
                                      If a landscape GIF is upside down, use 270.

              -f, --fit <mode>        How the GIF is scaled to the screen:
                                        contain  keep aspect, letterbox (default)
                                        cover    keep aspect, crop to fill
                                        stretch  fill exactly, distort aspect

              -s, --smooth            Smooth (bicubic) scaling. Default is
                                      nearest-neighbour, best for pixel art.

              -o, --output <dir>      Parent folder for the <name>_frames output.
                                      Default: next to the input GIF.
                                      Use the SD card drive (e.g. H:\) to write
                                      straight to the card.

              -c, --color <format>    Pixel format sent to the panel:
                                        444  12-bit, 4096 colours (default).
                                             Small enough to play from RAM
                                             at full speed.
                                        565  16-bit, 65536 colours. Large
                                             animations stream from SD and
                                             play at ~5 fps.

              --dither                Ordered dithering when reducing to 444.
                                      Hides banding in photos/gradients; leave
                                      off for pixel art.

              -b, --background <hex>  Letterbox / transparency colour, RRGGBB.
                                      Default: 000000 (black).

              -d, --delay <ms>        Frame delay used when the GIF has none.
                                      Default: 100.

              -h, --help              Show this help.

            OUTPUT
              <name>_frames/animation.json  metadata read by code.py
              <name>_frames/frames.bin      all 240x320 frames, back to back

            EXAMPLES
              GifConverter steve-minecraft.gif
              GifConverter bridget.gif -r 270 -f cover -o H:\
              GifConverter *.gif --smooth --dither
            """
        );
    }

    public static Options Parse(string[] args)
    {
        var options = new Options();

        if (args.Length == 0)
        {
            options.ShowHelp = true;
            return options;
        }

        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];

            string Value()
            {
                if (i + 1 >= args.Length)
                {
                    throw new ArgumentException($"Missing value for {arg}.");
                }

                return args[++i];
            }

            switch (arg.ToLowerInvariant())
            {
                case "-h":
                case "--help":
                case "-?":
                case "/?":
                    options.ShowHelp = true;
                    break;

                case "-r":
                case "--rotation":
                    options.Rotation = ParseRotation(Value());
                    break;

                case "-f":
                case "--fit":
                    options.Fit = ParseFit(Value());
                    break;

                case "-s":
                case "--smooth":
                    options.Smooth = true;
                    break;

                case "-o":
                case "--output":
                    options.OutputDirectory = Value();
                    break;

                case "-c":
                case "--color":
                    options.Format = Value() switch
                    {
                        "444" => ColorFormat.Rgb444,
                        "565" => ColorFormat.Rgb565,
                        _ => throw new ArgumentException("Color must be 444 or 565."),
                    };
                    break;

                case "--dither":
                    options.Dither = true;
                    break;

                case "-b":
                case "--background":
                    options.Background = ParseColor(Value());
                    break;

                case "-d":
                case "--delay":
                    if (!int.TryParse(Value(), out int delay) || delay <= 0)
                    {
                        throw new ArgumentException("Delay must be a positive number of milliseconds.");
                    }

                    options.DefaultDelayMs = delay;
                    break;

                default:
                    if (arg.StartsWith('-'))
                    {
                        throw new ArgumentException($"Unknown option: {arg}");
                    }

                    options.Inputs.AddRange(ExpandWildcards(arg));
                    break;
            }
        }

        if (!options.ShowHelp && options.Inputs.Count == 0)
        {
            throw new ArgumentException("No input GIF given.");
        }

        return options;
    }

    // PowerShell and cmd don't expand globs for native programs.
    static IEnumerable<string> ExpandWildcards(string path)
    {
        if (path.IndexOfAny(['*', '?']) < 0)
        {
            return [path];
        }

        string directory = Path.GetDirectoryName(path) is { Length: > 0 } d ? d : ".";
        string[] matches = Directory.GetFiles(directory, Path.GetFileName(path));

        if (matches.Length == 0)
        {
            throw new ArgumentException($"No files match {path}");
        }

        Array.Sort(matches, StringComparer.OrdinalIgnoreCase);
        return matches;
    }

    static int? ParseRotation(string value)
    {
        if (value.Equals("auto", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        if (int.TryParse(value, out int rotation) && rotation is 0 or 90 or 180 or 270)
        {
            return rotation;
        }

        throw new ArgumentException("Rotation must be auto, 0, 90, 180, or 270.");
    }

    static FitMode ParseFit(string value)
    {
        if (Enum.TryParse(value, ignoreCase: true, out FitMode fit) && Enum.IsDefined(fit))
        {
            return fit;
        }

        throw new ArgumentException("Fit must be contain, cover, or stretch.");
    }

    static Color ParseColor(string value)
    {
        string hex = value.TrimStart('#');

        if (hex.Length == 6 && int.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out int rgb))
        {
            return Color.FromArgb(rgb >> 16 & 0xFF, rgb >> 8 & 0xFF, rgb & 0xFF);
        }

        throw new ArgumentException("Background must be a hex colour like 000000 or #FF8800.");
    }
}


// ============================================================
// CONVERTER
// ============================================================

static class Converter
{
    // GDI+ property holding per-frame delays in 1/100ths of a second.
    const int FrameDelayPropertyId = 0x5100;

    public static void Convert(string inputFile, Options options, int panelWidth, int panelHeight)
    {
        if (!File.Exists(inputFile))
        {
            throw new FileNotFoundException($"File not found: {inputFile}");
        }

        string name = Path.GetFileNameWithoutExtension(inputFile);

        string outputParent = options.OutputDirectory
            ?? Path.GetDirectoryName(Path.GetFullPath(inputFile))!;

        string outputDirectory = Path.Combine(outputParent, name + "_frames");

        using var gif = Image.FromFile(inputFile);

        var dimension = new FrameDimension(gif.FrameDimensionsList[0]);
        int frameCount = gif.GetFrameCount(dimension);
        int[] delays = ReadFrameDelays(gif, frameCount, options.DefaultDelayMs);

        // The canvas has the dimensions the viewer sees. Landscape
        // rotations draw on a 320x240 canvas and are then turned to
        // land in the panel's native 240x320 order.
        int rotation = options.Rotation ?? (gif.Width > gif.Height ? 90 : 0);

        bool landscape = rotation is 90 or 270;
        int canvasWidth = landscape ? panelHeight : panelWidth;
        int canvasHeight = landscape ? panelWidth : panelHeight;

        Rectangle destination = FitRectangle(gif.Width, gif.Height, canvasWidth, canvasHeight, options.Fit);

        Console.WriteLine($"Input:      {inputFile} ({gif.Width}x{gif.Height}, {frameCount} frames)");
        Console.WriteLine($"Rotation:   {rotation} degrees{(options.Rotation == null ? " (auto)" : "")}");
        Console.WriteLine($"Fit:        {options.Fit.ToString().ToLowerInvariant()}, {(options.Smooth ? "smooth" : "nearest")} scaling");
        Console.WriteLine($"Color:      {FormatName(options.Format)}{(options.Dither && options.Format == ColorFormat.Rgb444 ? ", dithered" : "")}");
        Console.WriteLine($"Output:     {outputDirectory}");

        Directory.CreateDirectory(outputDirectory);

        // Everything is written to .tmp files and swapped in at the
        // end, so the Feather never reads a half-written animation.
        string framesFile = Path.Combine(outputDirectory, "frames.bin");
        string metadataFile = Path.Combine(outputDirectory, "animation.json");

        int frameSize = FrameSize(options.Format, panelWidth, panelHeight);
        byte[] encoded = new byte[frameSize];

        using var framesStream = new FileStream(framesFile + ".tmp", FileMode.Create, FileAccess.Write);

        RotateFlipType rotateFlip = rotation switch
        {
            90 => RotateFlipType.Rotate90FlipNone,
            180 => RotateFlipType.Rotate180FlipNone,
            270 => RotateFlipType.Rotate270FlipNone,
            _ => RotateFlipType.RotateNoneFlipNone,
        };

        for (int frameIndex = 0; frameIndex < frameCount; frameIndex++)
        {
            Console.Write($"\rConverting frame {frameIndex + 1}/{frameCount}...");

            gif.SelectActiveFrame(dimension, frameIndex);

            using var frame = new Bitmap(canvasWidth, canvasHeight, PixelFormat.Format24bppRgb);

            using (var graphics = Graphics.FromImage(frame))
            {
                graphics.Clear(options.Background);
                graphics.PixelOffsetMode = PixelOffsetMode.Half;

                graphics.InterpolationMode = options.Smooth
                    ? InterpolationMode.HighQualityBicubic
                    : InterpolationMode.NearestNeighbor;

                // Clamp edge sampling so scaled edges don't fade to black.
                using var attributes = new ImageAttributes();
                attributes.SetWrapMode(WrapMode.TileFlipXY);

                graphics.DrawImage(
                    gif,
                    destination,
                    0, 0, gif.Width, gif.Height,
                    GraphicsUnit.Pixel,
                    attributes);
            }

            frame.RotateFlip(rotateFlip);

            if (frame.Width != panelWidth || frame.Height != panelHeight)
            {
                throw new InvalidOperationException(
                    $"Internal error: frame is {frame.Width}x{frame.Height}, expected {panelWidth}x{panelHeight}.");
            }

            Encode(frame, options.Format, options.Dither, encoded);
            framesStream.Write(encoded);
        }

        framesStream.Dispose();

        Console.WriteLine();

        var metadata = new
        {
            width = panelWidth,
            height = panelHeight,
            frames = frameCount,
            format = FormatName(options.Format),
            frameSize,
            file = "frames.bin",
            // Informational only: pixels are already rotated.
            sourceRotation = rotation,
            durationsMs = delays,
        };

        File.WriteAllText(
            metadataFile + ".tmp",
            JsonSerializer.Serialize(metadata, new JsonSerializerOptions { WriteIndented = true }));

        // Per-frame files from older versions of the converter.
        foreach (string stale in Directory.EnumerateFiles(outputDirectory, "frame_*.rgb565"))
        {
            File.Delete(stale);
        }

        File.Move(framesFile + ".tmp", framesFile, overwrite: true);
        File.Move(metadataFile + ".tmp", metadataFile, overwrite: true);

        long totalBytes = (long)frameSize * frameCount;

        Console.WriteLine($"Done:       {frameCount} frames, {totalBytes / 1024.0 / 1024.0:F2} MB, {delays.Sum() / 1000.0:F1} s loop");
    }

    static string FormatName(ColorFormat format) => format switch
    {
        ColorFormat.Rgb444 => "RGB444",
        _ => "RGB565",
    };

    // RGB444 packs two pixels into three bytes; RGB565 uses two bytes per pixel.
    static int FrameSize(ColorFormat format, int width, int height) => format switch
    {
        ColorFormat.Rgb444 => width * height * 3 / 2,
        _ => width * height * 2,
    };

    // Scales the source into the canvas according to the fit mode.
    // The returned rectangle may extend past the canvas for "cover".
    static Rectangle FitRectangle(int sourceWidth, int sourceHeight, int canvasWidth, int canvasHeight, FitMode fit)
    {
        if (fit == FitMode.Stretch)
        {
            return new Rectangle(0, 0, canvasWidth, canvasHeight);
        }

        double scaleX = (double)canvasWidth / sourceWidth;
        double scaleY = (double)canvasHeight / sourceHeight;

        double scale = fit == FitMode.Contain
            ? Math.Min(scaleX, scaleY)
            : Math.Max(scaleX, scaleY);

        int width = (int)Math.Round(sourceWidth * scale);
        int height = (int)Math.Round(sourceHeight * scale);

        return new Rectangle(
            (canvasWidth - width) / 2,
            (canvasHeight - height) / 2,
            width,
            height);
    }

    static int[] ReadFrameDelays(Image gif, int frameCount, int defaultDelayMs)
    {
        var delays = new int[frameCount];
        byte[]? raw = null;

        if (gif.PropertyIdList.Contains(FrameDelayPropertyId))
        {
            raw = gif.GetPropertyItem(FrameDelayPropertyId)?.Value;
        }

        for (int i = 0; i < frameCount; i++)
        {
            int delayMs = 0;

            if (raw != null && (i + 1) * 4 <= raw.Length)
            {
                delayMs = BitConverter.ToInt32(raw, i * 4) * 10;
            }

            // Browsers treat 0 (and very small) delays as ~100 ms too.
            delays[i] = delayMs > 10 ? delayMs : defaultDelayMs;
        }

        return delays;
    }

    // 4x4 ordered-dither thresholds (0..15).
    static readonly int[,] Bayer4 =
    {
        { 0, 8, 2, 10 },
        { 12, 4, 14, 6 },
        { 3, 11, 1, 9 },
        { 15, 7, 13, 5 },
    };

    // Reduces an 8-bit channel to 4 bits, optionally dithered.
    static int To4Bit(int value, int x, int y, bool dither)
    {
        if (!dither)
        {
            return (value * 15 + 127) / 255;
        }

        // Round up when the fractional part passes this pixel's threshold.
        int threshold = (Bayer4[y & 3, x & 3] * 2 + 1) * 255 / 32;
        return (value * 15 + threshold) / 255;
    }

    // Encodes a 240x320 frame in the byte layout the ST7789 expects.
    static void Encode(Bitmap bitmap, ColorFormat format, bool dither, byte[] output)
    {
        var data = bitmap.LockBits(
            new Rectangle(0, 0, bitmap.Width, bitmap.Height),
            ImageLockMode.ReadOnly,
            PixelFormat.Format24bppRgb);

        try
        {
            byte[] bgr = new byte[bitmap.Width * 3];
            int o = 0;

            for (int y = 0; y < bitmap.Height; y++)
            {
                Marshal.Copy(data.Scan0 + y * data.Stride, bgr, 0, bgr.Length);

                if (format == ColorFormat.Rgb565)
                {
                    for (int x = 0; x < bitmap.Width; x++)
                    {
                        byte b = bgr[x * 3];
                        byte g = bgr[x * 3 + 1];
                        byte r = bgr[x * 3 + 2];

                        int rgb565 = (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3;

                        // Big-endian.
                        output[o++] = (byte)(rgb565 >> 8);
                        output[o++] = (byte)rgb565;
                    }
                }
                else
                {
                    // Two pixels -> three bytes: R1G1 B1R2 G2B2.
                    for (int x = 0; x < bitmap.Width; x += 2)
                    {
                        int b1 = To4Bit(bgr[x * 3], x, y, dither);
                        int g1 = To4Bit(bgr[x * 3 + 1], x, y, dither);
                        int r1 = To4Bit(bgr[x * 3 + 2], x, y, dither);
                        int b2 = To4Bit(bgr[x * 3 + 3], x + 1, y, dither);
                        int g2 = To4Bit(bgr[x * 3 + 4], x + 1, y, dither);
                        int r2 = To4Bit(bgr[x * 3 + 5], x + 1, y, dither);

                        output[o++] = (byte)(r1 << 4 | g1);
                        output[o++] = (byte)(b1 << 4 | r2);
                        output[o++] = (byte)(g2 << 4 | b2);
                    }
                }
            }
        }
        finally
        {
            bitmap.UnlockBits(data);
        }
    }

}
