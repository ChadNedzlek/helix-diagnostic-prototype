using System;
using System.Threading.Tasks;

namespace ProcessWithHang
{
    public class Program
    {
        static Task Main(string[] args)
        {
            return new TaskCompletionSource<object>().Task;
        }
    }
}
