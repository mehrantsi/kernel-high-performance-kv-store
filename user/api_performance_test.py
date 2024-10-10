import asyncio
import aiohttp
import time
import statistics
import random
import string
import json
from config import API_URL, API_KEY
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

headers = {
    "Content-Type": "application/json",
    "x-api-key": API_KEY
}

def generate_random_string(length):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def test_connection():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_URL}/ping", headers=headers) as response:
                if response.status == 200:
                    print("Successfully connected to the API.")
                    return True
                else:
                    print(f"API responded with status code: {response.status}")
                    return False
    except aiohttp.ClientError as e:
        print(f"Failed to connect to the API: {e}")
        return False

async def measure_api_call(session, method, url, **kwargs):
    start_time = time.time()
    async with getattr(session, method)(url, **kwargs) as response:
        end_time = time.time()
        if response.status == 200:
            return (end_time - start_time) * 1000  # Convert to milliseconds
        else:
            raise aiohttp.ClientError(f"API call failed: {await response.text()}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
       retry=retry_if_exception_type(aiohttp.ClientError))
async def insert_record(session, key, value):
    return await measure_api_call(session, 'post', f"{API_URL}/record", 
                                  headers=headers, json={"key": key, "value": value})

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
       retry=retry_if_exception_type(aiohttp.ClientError))
async def retrieve_record(session, key):
    return await measure_api_call(session, 'get', f"{API_URL}/record/{key}", headers=headers)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
       retry=retry_if_exception_type(aiohttp.ClientError))
async def delete_record(session, key):
    return await measure_api_call(session, 'delete', f"{API_URL}/record/{key}", headers=headers)

async def run_test(num_records, parallel=True, concurrency_limit=100):
    write_latencies = []
    read_latencies = []
    delete_latencies = []
    write_failures = 0
    read_failures = 0
    delete_failures = 0
    
    print(f"Running {'parallel' if parallel else 'sequential'} test with {num_records} records...")
    
    async with aiohttp.ClientSession() as session:
        overall_start_time = time.time()

        # Insert records
        insert_start_time = time.time()
        tasks = []
        for i in range(num_records):
            key = f"key_{num_records}_{i}"
            value = generate_random_string(50)  # Generate a random 50-character string
            tasks.append(insert_record(session, key, value))
        
        if parallel:
            semaphore = asyncio.Semaphore(concurrency_limit)
            async def bounded_insert(task):
                async with semaphore:
                    return await task
            write_results = await asyncio.gather(*[bounded_insert(task) for task in tasks], return_exceptions=True)
        else:
            write_results = []
            for task in tasks:
                try:
                    latency = await task
                    write_results.append(latency)
                except Exception as e:
                    write_results.append(e)
        
        insert_end_time = time.time()
        insert_total_time = insert_end_time - insert_start_time
        write_latencies = [lat for lat in write_results if isinstance(lat, (int, float))]
        write_failures = len(write_results) - len(write_latencies)
        
        # Read records
        read_start_time = time.time()
        tasks = []
        for i in range(num_records):
            key = f"key_{num_records}_{i}"
            tasks.append(retrieve_record(session, key))
        
        if parallel:
            async def bounded_retrieve(task):
                async with semaphore:
                    return await task
            read_results = await asyncio.gather(*[bounded_retrieve(task) for task in tasks], return_exceptions=True)
        else:
            read_results = []
            for task in tasks:
                try:
                    latency = await task
                    read_results.append(latency)
                except Exception as e:
                    read_results.append(e)
        
        read_end_time = time.time()
        read_total_time = read_end_time - read_start_time
        read_latencies = [lat for lat in read_results if isinstance(lat, (int, float))]
        read_failures = len(read_results) - len(read_latencies)
        
        # Delete records
        delete_start_time = time.time()
        tasks = []
        for i in range(num_records):
            key = f"key_{num_records}_{i}"
            tasks.append(delete_record(session, key))
        
        if parallel:
            async def bounded_delete(task):
                async with semaphore:
                    return await task
            delete_results = await asyncio.gather(*[bounded_delete(task) for task in tasks], return_exceptions=True)
        else:
            delete_results = []
            for task in tasks:
                try:
                    latency = await task
                    delete_results.append(latency)
                except Exception as e:
                    delete_results.append(e)
        
        delete_end_time = time.time()
        delete_total_time = delete_end_time - delete_start_time
        delete_latencies = [lat for lat in delete_results if isinstance(lat, (int, float))]
        delete_failures = len(delete_results) - len(delete_latencies)

        overall_end_time = time.time()
        overall_total_time = overall_end_time - overall_start_time
    
    if not write_latencies and not read_latencies and not delete_latencies:
        print("All operations failed. Please check the API connection and try again.")
        return

    # Calculate statistics
    def calculate_stats(latencies):
        return {
            "median": statistics.median(latencies),
            "mean": statistics.mean(latencies),
            "std_dev": statistics.stdev(latencies),
            "min": min(latencies),
            "max": max(latencies),
            "p95": sorted(latencies)[int(len(latencies) * 0.95)],
            "p99": sorted(latencies)[int(len(latencies) * 0.99)]
        }

    write_stats = calculate_stats(write_latencies)
    read_stats = calculate_stats(read_latencies)
    delete_stats = calculate_stats(delete_latencies)
    
    print(f"Results for {num_records} records:")
    print(f"Overall time: {overall_total_time:.3f} seconds")
    for op, stats, total_time, latencies, failures in [
        ("Write", write_stats, insert_total_time, write_latencies, write_failures), 
        ("Read", read_stats, read_total_time, read_latencies, read_failures), 
        ("Delete", delete_stats, delete_total_time, delete_latencies, delete_failures)
    ]:
        success_rate = (len(latencies) / num_records) * 100
        print(f"{op:<12} - Total time: {total_time:.3f} s, Throughput: {len(latencies)/total_time:.2f} ops/s")
        print(f"           Successful: {len(latencies)}, Failed: {failures}, Success Rate: {success_rate:.2f}%")
        if latencies:
            print(f"           Individual request stats:")
            print(f"           Median: {stats['median']:.3f} ms, Mean: {stats['mean']:.3f} ms, "
                  f"Std Dev: {stats['std_dev']:.3f} ms, Min: {stats['min']:.3f} ms, Max: {stats['max']:.3f} ms, "
                  f"P95: {stats['p95']:.3f} ms, P99: {stats['p99']:.3f} ms")
        else:
            print(f"           No successful requests to report statistics.")

async def main():
    if not await test_connection():
        print("Failed to connect to the API. Please check if the server is running and the API_URL is correct.")
        return

    sample_sizes = [100, 1000, 10000, 100000, 1000000]
    
    for size in sample_sizes:
        await run_test(size, parallel=True, concurrency_limit=1000)
        print()

if __name__ == "__main__":
    asyncio.run(main())