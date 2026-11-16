"""The files of a new C# project: one solution at the root, a library and an xUnit project. NuGet
downloads the packages when ``dotnet`` first builds."""

from forja.core.adapters import GITATTRIBUTES, ProjectTemplate, project_words

GITIGNORE = """\
bin/
obj/
.vs/
.forja-coverage/
"""
# The identifiers Visual Studio gives a C# project and a solution folder in a ``.sln``
_CSHARP_PROJECT = "FAE04EC0-301F-11D3-BF4B-00C04F79EFBC"
_LIBRARY_ID = "{6E0F2C1B-4B7D-4E2A-9B33-5D1A3B9C0A11}"
_TESTS_ID = "{B3D8F5A2-7C41-4F6E-8A92-2E5C7D1B4F22}"


def template(name: str) -> ProjectTemplate:
    pascal = "".join(word.capitalize() for word in project_words(name))
    tests = f"{pascal}.Tests"
    library_path = f"src/{pascal}/{pascal}.csproj"
    tests_path = f"tests/{tests}/{tests}.csproj"
    return ProjectTemplate(
        {
            f"{pascal}.sln": f"""\

Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
VisualStudioVersion = 17.0.31903.59
MinimumVisualStudioVersion = 10.0.40219.1
Project("{{{_CSHARP_PROJECT}}}") = "{pascal}", "{library_path.replace("/", "\\")}", "{_LIBRARY_ID}"
EndProject
Project("{{{_CSHARP_PROJECT}}}") = "{tests}", "{tests_path.replace("/", "\\")}", "{_TESTS_ID}"
EndProject
Global
	GlobalSection(SolutionConfigurationPlatforms) = preSolution
		Debug|Any CPU = Debug|Any CPU
		Release|Any CPU = Release|Any CPU
	EndGlobalSection
	GlobalSection(ProjectConfigurationPlatforms) = postSolution
		{_LIBRARY_ID}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
		{_LIBRARY_ID}.Debug|Any CPU.Build.0 = Debug|Any CPU
		{_LIBRARY_ID}.Release|Any CPU.ActiveCfg = Release|Any CPU
		{_LIBRARY_ID}.Release|Any CPU.Build.0 = Release|Any CPU
		{_TESTS_ID}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
		{_TESTS_ID}.Debug|Any CPU.Build.0 = Debug|Any CPU
		{_TESTS_ID}.Release|Any CPU.ActiveCfg = Release|Any CPU
		{_TESTS_ID}.Release|Any CPU.Build.0 = Release|Any CPU
	EndGlobalSection
	GlobalSection(SolutionProperties) = preSolution
		HideSolutionNode = FALSE
	EndGlobalSection
EndGlobal
""",
            library_path: """\
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>

</Project>
""",
            f"src/{pascal}/Version.cs": f"""\
namespace {pascal};

public static class Version
{{
    public const string Current = "0.1.0";
}}
""",
            tests_path: f"""\
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
    <PackageReference Include="coverlet.collector" Version="6.0.2" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="..\\..\\src\\{pascal}\\{pascal}.csproj" />
  </ItemGroup>

</Project>
""",
            f"tests/{tests}/VersionTests.cs": f"""\
using Xunit;

namespace {tests};

public class VersionTests
{{
    [Fact]
    public void TheProjectHasAVersion()
    {{
        Assert.Equal("0.1.0", {pascal}.Version.Current);
    }}
}}
""",
            ".gitignore": GITIGNORE,
            ".gitattributes": GITATTRIBUTES,
        }
    )
