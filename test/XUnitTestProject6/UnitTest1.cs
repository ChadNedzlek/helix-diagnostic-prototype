using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using Xunit;

namespace XUnitTestProject6
{
    public class UnitTest1
    {
        [Fact]
        public async Task OutOfProcessHang()
        {
            var path = Path.Combine(Path.GetDirectoryName(typeof(UnitTest1).Assembly.Location), "ProcessWithHang", "ProcessWithHang.dll");

            await ProcessUtil.RunAsync("dotnet", path);
        }
    }
}
