using System;
using System.Collections.Generic;
using System.Linq;
using Xunit;
using Xunit.Abstractions;
using Xunit.Sdk;

namespace Forja.Order;

// The ids the Forja asks for, one per line, in the order they run. A test that is not named runs after them.
internal static class Wanted
{
    public static readonly string[] Names =
        (Environment.GetEnvironmentVariable("FORJA_ORDER") ?? "")
        .Split('\n', StringSplitOptions.RemoveEmptyEntries);

    public static int Rank(string id)
    {
        int at = Array.IndexOf(Names, id);
        return at < 0 ? int.MaxValue : at;
    }

    // The class of an id: its name without the method, and without the arguments of a case.
    public static string ClassOf(string id)
    {
        string head = id.Split('(')[0];
        int dot = head.LastIndexOf('.');
        return dot < 0 ? "" : head.Substring(0, dot);
    }
}

// The collection of a class is named "Test collection for <class>" by xUnit.
public class CollectionOrderer : ITestCollectionOrderer
{
    public IEnumerable<ITestCollection> OrderTestCollections(IEnumerable<ITestCollection> collections) =>
        collections.OrderBy(c => Wanted.Names
            .Select((id, i) => (id, i))
            .Where(p => "Test collection for " + Wanted.ClassOf(p.id) == c.DisplayName)
            .Select(p => p.i)
            .DefaultIfEmpty(int.MaxValue)
            .Min());
}

// The display name of a case is its full id: the class, the method and the arguments.
public class CaseOrderer : ITestCaseOrderer
{
    public IEnumerable<TTestCase> OrderTestCases<TTestCase>(IEnumerable<TTestCase> testCases)
        where TTestCase : ITestCase =>
        testCases.OrderBy(t => Wanted.Rank(t.DisplayName));
}
