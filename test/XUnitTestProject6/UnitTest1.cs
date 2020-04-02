using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using Xunit;
using Xunit.Abstractions;

namespace XUnitTestProject6
{
    public class UnitTest1
    {
        private ITestOutputHelper _testOutputHelper;

        public UnitTest1(ITestOutputHelper testOutputHelper)
        {
            _testOutputHelper = testOutputHelper;
        }

        [Fact]
        public async Task OutOfProcessHang()
        {
            var path = Path.Combine(Path.GetDirectoryName(typeof(UnitTest1).Assembly.Location), "ProcessWithHang", "ProcessWithHang.dll");

            _testOutputHelper.WriteLine($"About to execute: {path}");

            await ProcessUtil.RunAsync("dotnet", path);
        }
    }
}
