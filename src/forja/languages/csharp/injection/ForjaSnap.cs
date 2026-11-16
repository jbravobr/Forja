using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Diagnostics.CodeAnalysis;
using System.Text.Json;
using System.Threading;
using Xunit;
using Xunit.Abstractions;
using Xunit.Sdk;

namespace Forja.Snap;

// After each test, the coverage it alone made: a snapshot of the session, in Cobertura, and its id
// in index.json. The tests run one at a time (serial runner file), so the index is written by one
// test at a time. Not the project's code: its lines are left out of the coverage.
[ExcludeFromCodeCoverage]
public class SnapFramework : XunitTestFramework
{
    public SnapFramework(IMessageSink diagnosticMessageSink) : base(diagnosticMessageSink) { }

    protected override ITestFrameworkExecutor CreateExecutor(System.Reflection.AssemblyName assemblyName) =>
        new SnapExecutor(assemblyName, SourceInformationProvider, DiagnosticMessageSink);
}

[ExcludeFromCodeCoverage]
public class SnapExecutor : XunitTestFrameworkExecutor
{
    public SnapExecutor(System.Reflection.AssemblyName assemblyName, ISourceInformationProvider sourceInformationProvider, IMessageSink diagnosticMessageSink)
        : base(assemblyName, sourceInformationProvider, diagnosticMessageSink) { }

    protected override async void RunTestCases(System.Collections.Generic.IEnumerable<IXunitTestCase> testCases, IMessageSink executionMessageSink, ITestFrameworkExecutionOptions executionOptions)
    {
        using var runner = new XunitTestAssemblyRunner(TestAssembly, testCases, DiagnosticMessageSink, new SnapSink(executionMessageSink), executionOptions);
        await runner.RunAsync();
    }
}

[ExcludeFromCodeCoverage]
public class SnapSink : LongLivedMarshalByRefObject, IMessageSink
{
    private readonly IMessageSink _inner;
    private static int _n;

    public SnapSink(IMessageSink inner) => _inner = inner;

    public bool OnMessage(IMessageSinkMessage message)
    {
        if (message is ITestFinished finished)
        {
            Record(finished.Test.DisplayName);
        }
        return _inner.OnMessage(message);
    }

    private static void Record(string id)
    {
        var dir = Environment.GetEnvironmentVariable("FORJA_SNAP")!;
        var dct = Environment.GetEnvironmentVariable("FORJA_DCT")!;
        var head = Environment.GetEnvironmentVariable("FORJA_DCT_HEAD") ?? "";
        var session = Environment.GetEnvironmentVariable("FORJA_SESSION")!;
        Directory.CreateDirectory(dir);
        var name = $"{Environment.ProcessId}-{Interlocked.Increment(ref _n):D4}";
        var coverage = Path.Combine(dir, name + ".coverage");
        var report = name + ".xml";
        Run(dct, $"{head} snapshot --reset -o \"{coverage}\" {session}");
        Run(dct, $"{head} merge -f cobertura -o \"{Path.Combine(dir, report)}\" \"{coverage}\"");
        File.Delete(coverage);
        AddToIndex(Path.Combine(dir, "index.json"), report, id);
    }

    private static void Run(string program, string arguments)
    {
        var psi = new ProcessStartInfo(program, arguments)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using var process = Process.Start(psi)!;
        process.StandardOutput.ReadToEnd();
        process.StandardError.ReadToEnd();
        process.WaitForExit();
    }

    private static void AddToIndex(string path, string report, string id)
    {
        var index = File.Exists(path)
            ? JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(path)) ?? new()
            : new Dictionary<string, string>();
        index[report] = id;
        File.WriteAllText(path, JsonSerializer.Serialize(index, new JsonSerializerOptions { WriteIndented = true }));
    }
}
